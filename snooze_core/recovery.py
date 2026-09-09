from __future__ import annotations

"""Crash recovery for supervisor and delivery state.

Recovery is explicit.  It never starts a command and it never guesses an exit
code from a missing process.  A valid result artifact is the only source used
to reconstruct a completed execution.
"""

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import (
    Delivery,
    DeliveryState,
    ExecutionState,
    LogInfo,
    Result,
    result_digest,
    result_hash_matches,
    utc_now,
)
from .process_identity import identity_matches, process_exists
from .store import JobStore, dict_to_identity


TERMINAL_EXECUTION_STATES = {
    ExecutionState.COMPLETED.value,
    ExecutionState.COMPLETED_STALE.value,
    ExecutionState.FAILED.value,
    ExecutionState.CANCELLED.value,
    ExecutionState.LOST.value,
    ExecutionState.ORPHANED.value,
}


def _supervisor_alive(metadata: Dict[str, Any]) -> bool:
    identity_value = metadata.get("supervisor_identity")
    if isinstance(identity_value, dict):
        identity = dict_to_identity(identity_value)
        if identity is not None:
            return identity_matches(identity, include_command=False)
    pid = metadata.get("supervisor_pid")
    return isinstance(pid, int) and process_exists(pid)


def _child_alive(metadata: Dict[str, Any]) -> bool:
    value = metadata.get("process_identity")
    if not isinstance(value, dict):
        return False
    identity = dict_to_identity(value)
    return identity is not None and identity_matches(identity, include_command=False)


def _valid_result(store: JobStore, job_id: str, result: Dict[str, Any]) -> Optional[str]:
    try:
        spec = store.read_spec(job_id)
    except (OSError, KeyError, ValueError) as exc:
        return f"spec unreadable: {type(exc).__name__}: {exc}"
    if result.get("job_id") != job_id:
        return "result job_id does not match directory"
    if result.get("spec_sha256") != spec.digest():
        return "result JobSpec digest does not match spec.json"
    if result.get("execution_state") not in TERMINAL_EXECUTION_STATES:
        return "result execution state is not terminal"
    if not result_hash_matches(result):
        return "result_sha256 does not match canonical result"
    return None


def _write_synthetic_loss(store: JobStore, job_id: str, state: ExecutionState, reason: str) -> Dict[str, Any]:
    metadata = store.read_metadata(job_id)
    spec = store.read_spec(job_id)
    logging = metadata.get("logging") or LogInfo().as_dict()
    result = Result(
        schema_version=1,
        job_id=job_id,
        spec_sha256=spec.digest(),
        execution_state=state,
        result_state="SUPERVISOR_ORPHANED" if state == ExecutionState.ORPHANED else "SUPERVISOR_LOST",
        exit_code=None,
        termination_signal=None,
        started_at=metadata.get("start_time"),
        completed_at=utc_now(),
        duration_seconds=None,
        process_identity=None,
        project_fingerprint_start=metadata.get("project_fingerprint_start"),
        project_fingerprint_end=metadata.get("project_fingerprint_end"),
        source_state_changed=metadata.get("stale"),
        logging=LogInfo(
            stdout_bytes=int(logging.get("stdout_bytes", 0)),
            stderr_bytes=int(logging.get("stderr_bytes", 0)),
            combined_bytes=int(logging.get("combined_bytes", 0)),
            stdout_truncated=bool(logging.get("stdout_truncated", False)),
            stderr_truncated=bool(logging.get("stderr_truncated", False)),
            combined_truncated=bool(logging.get("combined_truncated", False)),
            preview_truncated=bool(logging.get("preview_truncated", False)),
        ),
        log_preview="",
        completion_event_id=str(uuid.uuid4()),
        error_code="SUPERVISOR_LOST" if state == ExecutionState.LOST else "SUPERVISOR_ORPHANED",
        error_message=reason,
    )
    result_payload = result.as_dict()
    result.result_sha256 = result_digest(result_payload)
    store.write_result(job_id, result)
    store.reconcile_metadata_from_result(job_id, result.as_dict())
    store.write_delivery(
        job_id,
        Delivery(
            schema_version=1,
            job_id=job_id,
            completion_event_id=result.completion_event_id,
            result_sha256=result.result_sha256,
            state=DeliveryState.PENDING,
        ),
    )
    return result.as_dict()


def _repair_delivery(store: JobStore, job_id: str, result: Dict[str, Any], report: Dict[str, Any]) -> None:
    expected_event = result.get("completion_event_id")
    expected_hash = result.get("result_sha256")
    try:
        delivery = store.read_delivery(job_id)
    except (OSError, ValueError, KeyError):
        delivery = {
            "schema_version": 1,
            "job_id": job_id,
            "completion_event_id": None,
            "result_sha256": None,
            "state": DeliveryState.NOT_READY.value,
            "attempts": 0,
        }

    delivery["job_id"] = job_id
    delivery["completion_event_id"] = expected_event
    delivery["result_sha256"] = expected_hash
    state = DeliveryState(delivery.get("state", DeliveryState.NOT_READY.value))
    payload_path = store.job_dir(job_id) / "delivery_payload.json"
    payload_valid = False
    if payload_path.exists():
        try:
            payload = json.loads(payload_path.read_text(encoding="utf-8"))
            payload_valid = (
                payload.get("job_id") == job_id
                and (payload.get("event_id") or payload.get("completion_event_id")) == expected_event
                and payload.get("result_sha256") == expected_hash
            )
        except (OSError, ValueError, AttributeError):
            payload_valid = False

    if state in {DeliveryState.CLAIMED, DeliveryState.SENDING}:
        if payload_valid:
            delivery["state"] = DeliveryState.SENT_UNCONFIRMED.value
            delivery["transport"] = delivery.get("transport") or "manual-file"
            delivery["remote_marker"] = delivery.get("remote_marker") or payload_path.name
            delivery["error_code"] = None
            delivery["error_message"] = None
            report["delivery_repaired"] = True
        else:
            delivery["state"] = DeliveryState.RETRY_WAIT.value
            delivery["error_code"] = "DELIVERY_INTERRUPTED"
            delivery["error_message"] = "delivery state was in-flight when recovery ran"
            report["delivery_retry_required"] = True
    elif state == DeliveryState.NOT_READY:
        delivery["state"] = DeliveryState.PENDING.value
        report["delivery_repaired"] = True
    elif state in {DeliveryState.PENDING, DeliveryState.RETRY_WAIT, DeliveryState.FAILED}:
        pass
    elif state == DeliveryState.SENT_UNCONFIRMED and not payload_valid:
        delivery["state"] = DeliveryState.RETRY_WAIT.value
        delivery["error_code"] = "DELIVERY_PAYLOAD_MISSING"
        delivery["error_message"] = "delivery state referenced a missing or mismatched payload"
        report["delivery_retry_required"] = True
    delivery["updated_at"] = utc_now()
    store.write_delivery_dict(job_id, delivery)


def recover_job(store: JobStore, job_id: str) -> Dict[str, Any]:
    metadata = store.read_metadata(job_id)
    report: Dict[str, Any] = {
        "job_id": job_id,
        "before_execution_state": metadata.get("execution_state"),
        "action": "none",
    }
    result = store.read_result(job_id)
    if result is not None:
        invalid = _valid_result(store, job_id, result)
        if invalid is not None:
            report["action"] = "result_invalid"
            report["error"] = invalid
            return report
        if metadata.get("execution_state") != result.get("execution_state") or metadata.get("result_state") != result.get("result_state"):
            store.reconcile_metadata_from_result(job_id, result)
            report["action"] = "metadata_reconciled_from_result"
        _repair_delivery(store, job_id, result, report)
        report["after_execution_state"] = result.get("execution_state")
        return report

    state = metadata.get("execution_state")
    if state in TERMINAL_EXECUTION_STATES:
        report["action"] = "terminal_result_missing"
        result = _write_synthetic_loss(store, job_id, ExecutionState.LOST, "terminal metadata existed without result.json")
        report["after_execution_state"] = result.get("execution_state")
        report["synthetic_result"] = True
        return report

    supervisor_alive = _supervisor_alive(metadata)
    child_alive = _child_alive(metadata)
    report["supervisor_alive"] = supervisor_alive
    report["child_alive"] = child_alive
    if supervisor_alive:
        report["action"] = "still_running"
        report["after_execution_state"] = state
        return report

    if state == ExecutionState.SUBMITTED.value:
        terminal_state = ExecutionState.ORPHANED
        reason = "job was submitted but no supervisor was found"
    elif child_alive:
        terminal_state = ExecutionState.ORPHANED
        reason = "supervisor disappeared while the child process identity was still present"
    else:
        terminal_state = ExecutionState.LOST
        reason = "supervisor and child process were not present; exit status is unavailable"
    result = _write_synthetic_loss(store, job_id, terminal_state, reason)
    report["action"] = "synthetic_terminal_result"
    report["after_execution_state"] = result.get("execution_state")
    report["synthetic_result"] = True
    return report


def recover_store(store_root: Path, job_id: Optional[str] = None) -> List[Dict[str, Any]]:
    store = JobStore(store_root)
    job_ids = [job_id] if job_id else list(store.list_job_ids())
    reports: List[Dict[str, Any]] = []
    for current_job_id in job_ids:
        try:
            reports.append(recover_job(store, current_job_id))
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
            reports.append(
                {
                    "job_id": current_job_id,
                    "action": "recovery_error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    return reports


__all__ = ["recover_job", "recover_store"]
