from __future__ import annotations

"""v0.5 backend ownership and security-gated selection.

The v0.5 controller observes ownership transitions; it does not start a
command for either of the security-preserving candidates.  A caller must
provide evidence from the normal Codex execution path before the selector can
return a candidate backend.  Unknown or partial evidence always falls back to
the explicit CLI resume path.
"""

import fcntl
import hashlib
import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from snooze_core.persistence import atomic_write_json
from snooze_core.models import utc_now


class V05Backend(str, Enum):
    THREAD_NATIVE_TERMINAL = "THREAD_NATIVE_TERMINAL"
    DESCENDANT_SUPERVISOR = "SANDBOX_DESCENDANT_SUPERVISOR"
    CONTROLLER_COMMAND_EXEC = "CONTROLLER_COMMAND_EXEC"
    CLI_RESUME_FALLBACK = "CLI_RESUME_FALLBACK"


class OwnershipState(str, Enum):
    FOREGROUND = "FOREGROUND"
    HANDOFF_PENDING = "HANDOFF_PENDING"
    NATIVE_BACKGROUND = "NATIVE_BACKGROUND"
    SUPERVISOR_BACKGROUND = "SUPERVISOR_BACKGROUND"
    COMPLETING = "COMPLETING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


OWNERSHIP_TRANSITIONS = {
    OwnershipState.FOREGROUND: {OwnershipState.HANDOFF_PENDING, OwnershipState.FAILED},
    OwnershipState.HANDOFF_PENDING: {
        OwnershipState.NATIVE_BACKGROUND,
        OwnershipState.SUPERVISOR_BACKGROUND,
        OwnershipState.COMPLETING,
        OwnershipState.FAILED,
    },
    OwnershipState.NATIVE_BACKGROUND: {OwnershipState.COMPLETING, OwnershipState.FAILED},
    OwnershipState.SUPERVISOR_BACKGROUND: {OwnershipState.COMPLETING, OwnershipState.FAILED},
    OwnershipState.COMPLETING: {OwnershipState.COMPLETED, OwnershipState.FAILED},
    OwnershipState.COMPLETED: set(),
    OwnershipState.FAILED: set(),
}


@dataclass(frozen=True)
class PendingHandoff:
    handoff_id: str
    job_id: str
    thread_id: str
    turn_id: str
    expected_command_sha256: str
    threshold_seconds: float
    backend: V05Backend
    state: OwnershipState = OwnershipState.FOREGROUND

    def as_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["backend"] = self.backend.value
        value["state"] = self.state.value
        return value


def command_sha256(command: str) -> str:
    """Hash the exact semantic command string, including whitespace."""
    if not isinstance(command, str) or not command:
        raise ValueError("command must be a non-empty string")
    return hashlib.sha256(command.encode("utf-8")).hexdigest()


def command_integrity(
    requested_command: str,
    observed_command: Optional[str],
    executed_command: Optional[str],
) -> Dict[str, Any]:
    """Compare command evidence without treating the hash as approval proof."""
    values = {
        "requested_command_sha256": command_sha256(requested_command),
        "observed_command_sha256": command_sha256(observed_command) if observed_command else None,
        "executed_command_sha256": command_sha256(executed_command) if executed_command else None,
    }
    values["status"] = (
        "PASS"
        if observed_command == requested_command and executed_command == requested_command
        else "UNKNOWN"
        if observed_command is None or executed_command is None
        else "FAIL"
    )
    values["approval_semantics_proven"] = False
    return values


def security_ready(status: str) -> bool:
    return str(status) == "PASS"


def select_backend(evidence: Dict[str, Any]) -> V05Backend:
    """Select only a fully proven security-preserving backend."""
    native = evidence.get("THREAD_NATIVE_TERMINAL", {}) or {}
    if all(
        security_ready(native.get(key))
        for key in (
            "status",
            "sandbox_parity",
            "approval_parity",
            "handoff_10s",
            "job_survival",
            "model_idle",
            "auto_continuation",
            "command_integrity",
        )
    ):
        return V05Backend.THREAD_NATIVE_TERMINAL
    descendant = evidence.get("SANDBOX_DESCENDANT_SUPERVISOR", {}) or {}
    if all(
        security_ready(descendant.get(key))
        for key in (
            "status",
            "sandbox_parity",
            "approval_parity",
            "handoff_10s",
            "job_survival",
            "model_idle",
            "auto_continuation",
            "command_integrity",
        )
    ):
        return V05Backend.DESCENDANT_SUPERVISOR
    return V05Backend.CLI_RESUME_FALLBACK


class OwnershipLedger:
    """A small durable compare-and-swap ledger for one backend owner per job."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self._thread_lock = threading.RLock()

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._thread_lock, self.lock_path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "jobs": {}}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("jobs", {}), dict):
            raise ValueError("ownership ledger is not a valid object")
        return value

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._locked():
            return self._read().get("jobs", {}).get(job_id)

    def create(self, handoff: PendingHandoff) -> Dict[str, Any]:
        with self._locked():
            value = self._read()
            if handoff.job_id in value["jobs"]:
                raise ValueError("job already has an ownership record")
            record = handoff.as_dict()
            record["created_at"] = utc_now()
            record["updated_at"] = record["created_at"]
            value["jobs"][handoff.job_id] = record
            atomic_write_json(self.path, value)
            return dict(record)

    def transition(
        self,
        job_id: str,
        *,
        expected: OwnershipState,
        target: OwnershipState,
        backend: Optional[V05Backend] = None,
    ) -> Dict[str, Any]:
        with self._locked():
            value = self._read()
            record = value.get("jobs", {}).get(job_id)
            if record is None:
                raise KeyError(job_id)
            current = OwnershipState(record["state"])
            if current != expected:
                raise ValueError(f"ownership compare-and-swap failed: {current.value} != {expected.value}")
            if target != current and target not in OWNERSHIP_TRANSITIONS[current]:
                raise ValueError(f"invalid ownership transition {current.value}->{target.value}")
            if target in {OwnershipState.NATIVE_BACKGROUND, OwnershipState.SUPERVISOR_BACKGROUND}:
                if backend is None:
                    raise ValueError("background ownership requires an explicit backend")
                expected_backend = (
                    V05Backend.THREAD_NATIVE_TERMINAL
                    if target is OwnershipState.NATIVE_BACKGROUND
                    else V05Backend.DESCENDANT_SUPERVISOR
                )
                if backend is not expected_backend:
                    raise ValueError("backend does not match ownership state")
                if record.get("backend") != backend.value:
                    raise ValueError("record backend does not match transfer backend")
            record["state"] = target.value
            record["updated_at"] = utc_now()
            if backend is not None and record.get("backend") != backend.value:
                raise ValueError("backend owner cannot change during transfer")
            value["jobs"][job_id] = record
            atomic_write_json(self.path, value)
            return dict(record)


__all__ = [
    "OwnershipLedger",
    "OwnershipState",
    "PendingHandoff",
    "V05Backend",
    "command_integrity",
    "command_sha256",
    "select_backend",
]
