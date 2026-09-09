from __future__ import annotations

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional

from .models import (
    Delivery,
    DeliveryState,
    ExecutionState,
    JobSpec,
    LogInfo,
    Metadata,
    ProcessIdentity,
    Result,
    deserialize_spec,
    load_json,
    utc_now,
)
from .persistence import atomic_write_json


EXECUTION_TRANSITIONS = {
    ExecutionState.SUBMITTED: {ExecutionState.STARTING, ExecutionState.FAILED},
    ExecutionState.STARTING: {ExecutionState.RUNNING, ExecutionState.FAILED, ExecutionState.LOST},
    ExecutionState.RUNNING: {
        ExecutionState.COMPLETED,
        ExecutionState.COMPLETED_STALE,
        ExecutionState.FAILED,
        ExecutionState.CANCELLED,
        ExecutionState.LOST,
        ExecutionState.ORPHANED,
    },
    ExecutionState.COMPLETED: set(),
    ExecutionState.COMPLETED_STALE: set(),
    ExecutionState.FAILED: set(),
    ExecutionState.CANCELLED: set(),
    ExecutionState.LOST: set(),
    ExecutionState.ORPHANED: set(),
}


DELIVERY_TRANSITIONS = {
    DeliveryState.NOT_READY: {DeliveryState.PENDING},
    DeliveryState.PENDING: {DeliveryState.CLAIMED, DeliveryState.FAILED},
    DeliveryState.CLAIMED: {DeliveryState.SENDING, DeliveryState.PENDING, DeliveryState.FAILED},
    DeliveryState.SENDING: {
        DeliveryState.SENT_UNCONFIRMED,
        DeliveryState.PENDING,
        DeliveryState.RETRY_WAIT,
        DeliveryState.FAILED,
    },
    DeliveryState.SENT_UNCONFIRMED: {
        DeliveryState.ACKED,
        DeliveryState.PENDING,
        DeliveryState.RETRY_WAIT,
        DeliveryState.CLAIMED,
    },
    DeliveryState.ACKED: set(),
    DeliveryState.RETRY_WAIT: {DeliveryState.PENDING, DeliveryState.CLAIMED, DeliveryState.FAILED},
    DeliveryState.FAILED: {DeliveryState.PENDING, DeliveryState.CLAIMED},
}


class JobStore:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.jobs = self.root / "jobs"
        self.jobs.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.root, 0o700)
            os.chmod(self.jobs, 0o700)
        except OSError:
            pass

    def job_dir(self, job_id: str) -> Path:
        if Path(job_id).name != job_id or job_id in ("", ".", ".."):
            raise ValueError("invalid job id")
        return self.jobs / job_id

    @contextmanager
    def _lock(self, job_id: str, name: str) -> Iterator[None]:
        path = self.job_dir(job_id) / name
        with path.open("a+") as handle:
            os.fchmod(handle.fileno(), 0o600)
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def create(self, spec: JobSpec) -> Path:
        directory = self.job_dir(spec.job_id)
        directory.mkdir(mode=0o700)
        atomic_write_json(directory / "spec.json", spec.as_dict())
        atomic_write_json(
            directory / "metadata.json",
            {
                "job_id": spec.job_id,
                "spec_sha256": spec.digest(),
                "command": spec.command,
                "cwd": spec.cwd,
                "shell": spec.shell,
                "execution_state": ExecutionState.SUBMITTED.value,
                "result_state": None,
            "supervisor_pid": None,
                "supervisor_identity": None,
                "pid": None,
                "process_group": None,
                "start_time": None,
                "finish_time": None,
                "exit_code": None,
                "termination_signal": None,
                "process_identity": None,
                "project_fingerprint_start": None,
                "project_fingerprint_end": None,
                "stale": None,
                "logging": LogInfo().as_dict(),
                "error_code": None,
                "error_message": None,
                "cancellation_requested_at": None,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            },
        )
        atomic_write_json(
            directory / "delivery.json",
            Delivery(
                schema_version=1,
                job_id=spec.job_id,
                completion_event_id=None,
                result_sha256=None,
                state=DeliveryState.NOT_READY,
            ).as_dict(),
        )
        return directory

    def read_spec(self, job_id: str) -> JobSpec:
        spec = deserialize_spec(load_json(self.job_dir(job_id) / "spec.json"))
        if spec.job_id != job_id:
            raise ValueError("spec job_id does not match its job directory")
        return spec

    def read_metadata(self, job_id: str) -> Dict[str, Any]:
        return load_json(self.job_dir(job_id) / "metadata.json")

    def write_metadata(self, job_id: str, value: Dict[str, Any]) -> None:
        with self._lock(job_id, ".metadata.lock"):
            self._write_metadata_unlocked(job_id, value)

    def _write_metadata_unlocked(self, job_id: str, value: Dict[str, Any]) -> None:
        value = dict(value)
        value["updated_at"] = utc_now()
        atomic_write_json(self.job_dir(job_id) / "metadata.json", value)

    def update_metadata(self, job_id: str, **changes: Any) -> Dict[str, Any]:
        with self._lock(job_id, ".metadata.lock"):
            value = self.read_metadata(job_id)
            current = ExecutionState(value["execution_state"])
            requested = changes.get("execution_state")
            if requested is not None:
                target = ExecutionState(requested)
                if target != current and target not in EXECUTION_TRANSITIONS[current]:
                    raise ValueError(f"invalid execution transition {current.value}->{target.value}")
                changes["execution_state"] = target.value
            value.update(changes)
            self._write_metadata_unlocked(job_id, value)
            return value

    def write_result(self, job_id: str, result: Result) -> None:
        atomic_write_json(self.job_dir(job_id) / "result.json", result.as_dict())

    def read_result(self, job_id: str) -> Optional[Dict[str, Any]]:
        path = self.job_dir(job_id) / "result.json"
        if not path.exists():
            return None
        return load_json(path)

    def write_delivery(self, job_id: str, delivery: Delivery) -> None:
        self.write_delivery_dict(job_id, delivery.as_dict())

    def write_delivery_dict(self, job_id: str, value: Dict[str, Any]) -> None:
        with self._lock(job_id, ".delivery.lock"):
            atomic_write_json(self.job_dir(job_id) / "delivery.json", dict(value))

    def read_delivery(self, job_id: str) -> Dict[str, Any]:
        return load_json(self.job_dir(job_id) / "delivery.json")

    def update_delivery(self, job_id: str, **changes: Any) -> Dict[str, Any]:
        with self._lock(job_id, ".delivery.lock"):
            value = self.read_delivery(job_id)
            current = DeliveryState(value["state"])
            requested = changes.get("state")
            if requested is not None:
                target = DeliveryState(requested)
                if target != current and target not in DELIVERY_TRANSITIONS[current]:
                    raise ValueError(f"invalid delivery transition {current.value}->{target.value}")
                changes["state"] = target.value
            value.update(changes)
            value["updated_at"] = utc_now()
            atomic_write_json(self.job_dir(job_id) / "delivery.json", value)
            return value

    def claim_delivery(self, job_id: str, allowed_states: set[DeliveryState], **changes: Any) -> Dict[str, Any]:
        """Atomically claim a delivery so two controllers cannot both claim PENDING."""
        with self._lock(job_id, ".delivery.lock"):
            value = self.read_delivery(job_id)
            current = DeliveryState(value["state"])
            if current not in allowed_states:
                raise ValueError(f"delivery changed before claim; currently {current.value}")
            if DeliveryState.CLAIMED not in DELIVERY_TRANSITIONS[current]:
                raise ValueError(f"invalid delivery claim from {current.value}")
            value.update(changes)
            value["state"] = DeliveryState.CLAIMED.value
            value["attempts"] = int(value.get("attempts", 0)) + 1
            value["updated_at"] = utc_now()
            atomic_write_json(self.job_dir(job_id) / "delivery.json", value)
            return value

    def reconcile_metadata_from_result(self, job_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """Repair metadata from a validated terminal result after a crash window."""
        with self._lock(job_id, ".metadata.lock"):
            value = self.read_metadata(job_id)
            field_map = {
                "execution_state": "execution_state",
                "result_state": "result_state",
                "finish_time": "completed_at",
                "exit_code": "exit_code",
                "termination_signal": "termination_signal",
                "project_fingerprint_end": "project_fingerprint_end",
                "stale": "source_state_changed",
                "logging": "logging",
                "error_code": "error_code",
                "error_message": "error_message",
            }
            for metadata_key, result_key in field_map.items():
                if result_key in result:
                    value[metadata_key] = result.get(result_key)
            if result.get("process_identity") is not None:
                value["process_identity"] = result["process_identity"]
            self._write_metadata_unlocked(job_id, value)
            return value

    def list_job_ids(self) -> Iterable[str]:
        if not self.jobs.exists():
            return []
        return sorted(
            entry.name
            for entry in self.jobs.iterdir()
            if entry.is_dir() and entry.name not in (".", "..")
        )


def dict_to_identity(value: Optional[Dict[str, Any]]) -> Optional[ProcessIdentity]:
    if value is None:
        return None
    return ProcessIdentity(
        pid=int(value["pid"]),
        pgid=int(value["pgid"]) if value.get("pgid") is not None else None,
        start_time=value.get("start_time"),
        executable=value.get("executable"),
        command_fingerprint=value.get("command_fingerprint"),
    )
