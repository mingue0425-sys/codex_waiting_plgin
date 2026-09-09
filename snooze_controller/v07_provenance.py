from __future__ import annotations

"""Strict v0.7 identity and state gates.

The App Server ``processId`` field is deliberately kept separate from an OS
PID.  A native handoff may only be considered owned when independent evidence
connects the command item, the probe output, the workspace marker, and the
observed runtime identity.  Missing evidence is UNKNOWN; a contradiction is
FAIL.  This module never starts, stops, signals, or writes to a candidate
process.
"""

import enum
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional


class ProcessIdKind(str, enum.Enum):
    OS_PID = "OS_PID"
    LOGICAL_HANDLE = "LOGICAL_HANDLE"
    OPAQUE = "OPAQUE"
    UNKNOWN = "UNKNOWN"


class NativeState(str, enum.Enum):
    REQUESTED = "REQUESTED"
    COMMAND_ITEM_STARTED = "COMMAND_ITEM_STARTED"
    PROVENANCE_PENDING = "PROVENANCE_PENDING"
    PROVENANCE_ESTABLISHED = "PROVENANCE_ESTABLISHED"
    FOREGROUND_RUNNING = "FOREGROUND_RUNNING"
    YIELDED = "YIELDED"
    HANDOFF_PENDING = "HANDOFF_PENDING"
    TURN_CLOSED = "TURN_CLOSED"
    BACKGROUND_RUNNING = "BACKGROUND_RUNNING"
    COMPLETING = "COMPLETING"
    COMPLETED = "COMPLETED"
    CONTINUATION_PENDING = "CONTINUATION_PENDING"
    CONTINUED = "CONTINUED"
    FALLBACK = "FALLBACK"


_TRANSITIONS: Dict[NativeState, set[NativeState]] = {
    NativeState.REQUESTED: {NativeState.COMMAND_ITEM_STARTED, NativeState.FALLBACK},
    NativeState.COMMAND_ITEM_STARTED: {NativeState.PROVENANCE_PENDING, NativeState.FALLBACK},
    NativeState.PROVENANCE_PENDING: {NativeState.PROVENANCE_ESTABLISHED, NativeState.FALLBACK},
    NativeState.PROVENANCE_ESTABLISHED: {NativeState.FOREGROUND_RUNNING, NativeState.FALLBACK},
    NativeState.FOREGROUND_RUNNING: {NativeState.YIELDED, NativeState.COMPLETING, NativeState.FALLBACK},
    NativeState.YIELDED: {NativeState.HANDOFF_PENDING, NativeState.FALLBACK},
    NativeState.HANDOFF_PENDING: {NativeState.TURN_CLOSED, NativeState.FALLBACK},
    NativeState.TURN_CLOSED: {NativeState.BACKGROUND_RUNNING, NativeState.FALLBACK},
    NativeState.BACKGROUND_RUNNING: {NativeState.COMPLETING, NativeState.FALLBACK},
    NativeState.COMPLETING: {NativeState.COMPLETED, NativeState.FALLBACK},
    NativeState.COMPLETED: {NativeState.CONTINUATION_PENDING, NativeState.FALLBACK},
    NativeState.CONTINUATION_PENDING: {NativeState.CONTINUED, NativeState.FALLBACK},
    NativeState.CONTINUED: set(),
    NativeState.FALLBACK: set(),
}


class InvalidNativeTransition(ValueError):
    pass


@dataclass
class NativeStateMachine:
    state: NativeState = NativeState.REQUESTED
    history: list[NativeState] = field(default_factory=lambda: [NativeState.REQUESTED])

    def transition(self, target: NativeState, *, provenance_established: bool = False) -> NativeState:
        if target == NativeState.TURN_CLOSED and self.state in {
            NativeState.COMMAND_ITEM_STARTED,
            NativeState.PROVENANCE_PENDING,
            NativeState.FOREGROUND_RUNNING,
        } and not provenance_established:
            raise InvalidNativeTransition("turn cannot close before provenance is established")
        if target not in _TRANSITIONS.get(self.state, set()):
            raise InvalidNativeTransition(f"{self.state.value} -> {target.value} is not valid")
        if target in {NativeState.YIELDED, NativeState.HANDOFF_PENDING, NativeState.BACKGROUND_RUNNING} and not provenance_established:
            raise InvalidNativeTransition("native ownership state requires provenance")
        self.state = target
        self.history.append(target)
        return target


def _path_equal(left: Any, right: Any) -> Optional[bool]:
    if left is None or right is None:
        return None
    try:
        return Path(str(left)).resolve() == Path(str(right)).resolve()
    except (OSError, RuntimeError):
        return str(left) == str(right)


def _bool(value: Optional[bool]) -> str:
    if value is True:
        return "PASS"
    if value is False:
        return "FAIL"
    return "UNKNOWN"


def classify_process_id(
    logical_process_id: Any,
    *,
    self_reported_os_pid: Any = None,
    observed_os_pid: Any = None,
    source: str = "",
) -> ProcessIdKind:
    """Classify an identifier without assuming the field name means OS PID."""

    if logical_process_id is None:
        return ProcessIdKind.UNKNOWN
    if self_reported_os_pid is not None and observed_os_pid is not None:
        try:
            if int(logical_process_id) == int(self_reported_os_pid) == int(observed_os_pid):
                return ProcessIdKind.OS_PID
        except (TypeError, ValueError):
            pass
    # The installed protocol describes standalone command/exec IDs as
    # connection-scoped. A thread/background processId is an App Server
    # identity when host metadata is separately nullable or different.
    if source in {"command/exec", "process/spawn"}:
        return ProcessIdKind.LOGICAL_HANDLE
    if self_reported_os_pid is not None or observed_os_pid is not None:
        return ProcessIdKind.LOGICAL_HANDLE
    if isinstance(logical_process_id, str):
        return ProcessIdKind.OPAQUE
    return ProcessIdKind.UNKNOWN


def evidence_status(evidence: Mapping[str, Optional[bool]], *, required: Iterable[str]) -> str:
    values = [evidence.get(name) for name in required]
    if any(value is False for value in values):
        return "FAIL"
    if all(value is True for value in values):
        return "PASS"
    return "UNKNOWN"


def correlate_probe_evidence(
    *,
    expected_nonce: Optional[str],
    item_record: Mapping[str, Any],
    output_text: Optional[str],
    marker_record: Optional[Mapping[str, Any]],
    self_report: Optional[Mapping[str, Any]],
    os_observation: Optional[Mapping[str, Any]],
    expected_cwd: Optional[str],
    expected_command: Optional[str] = None,
    logical_process_id: Any = None,
) -> Dict[str, Any]:
    """Evaluate the independent evidence set used by the v0.7 gate."""

    output = output_text or ""
    marker = marker_record or {}
    reported = self_report or {}
    observed = os_observation or {}
    event_cwd = item_record.get("cwd")
    report_cwd = reported.get("cwd")
    observed_cwd = observed.get("cwd")
    report_pid = reported.get("pid")
    observed_pid = observed.get("pid")

    if expected_nonce is None:
        nonce_in_item = None
    elif expected_nonce in str(item_record):
        nonce_in_item = True
    elif any(key in item_record for key in ("nonce", "probe_nonce", "probeNonce")) or "SNOOZE_PROBE_" in str(item_record):
        nonce_in_item = False
    else:
        nonce_in_item = None
    nonce_in_output = None if expected_nonce is None or not output else expected_nonce in output
    marker_nonce = None if expected_nonce is None or not marker else marker.get("nonce") == expected_nonce
    pid_observed: Optional[bool] = None if report_pid is None else isinstance(report_pid, int) and report_pid > 0
    command_value = item_record.get("command")
    command_match = None if expected_command is None or command_value is None else command_value == expected_command
    cwd_match: Optional[bool]
    if expected_cwd is None:
        cwd_match = None
    else:
        cwd_match = _path_equal(expected_cwd, event_cwd)
        if cwd_match is True and report_cwd is not None:
            cwd_match = _path_equal(expected_cwd, report_cwd)
        if cwd_match is True and observed_cwd is not None:
            cwd_match = _path_equal(expected_cwd, observed_cwd)
    os_command_match = observed.get("command_match")
    os_start_match = observed.get("start_identity_match")
    if os_start_match is None and reported.get("start_identity_ns") is not None and observed.get("start_identity_ns") is not None:
        os_start_match = reported.get("start_identity_ns") == observed.get("start_identity_ns")
    if observed_pid is not None and report_pid is not None:
        try:
            pid_match = int(observed_pid) == int(report_pid)
        except (TypeError, ValueError):
            pid_match = False
    else:
        pid_match = None

    evidence: Dict[str, Optional[bool]] = {
        "ITEM_NONCE_MATCH": nonce_in_item,
        "STDOUT_NONCE_MATCH": nonce_in_output,
        "WORKSPACE_MARKER_MATCH": marker_nonce,
        "SELF_REPORTED_PID_OBSERVED": pid_observed,
        "OS_COMMAND_MATCH": os_command_match,
        "OS_START_TIME_MATCH": os_start_match,
        "CWD_MATCH": cwd_match,
    }
    required = (
        "ITEM_NONCE_MATCH",
        "STDOUT_NONCE_MATCH",
        "WORKSPACE_MARKER_MATCH",
        "SELF_REPORTED_PID_OBSERVED",
        "CWD_MATCH",
    )
    status = evidence_status(evidence, required=required)
    if command_match is False:
        status = "FAIL"
    process_id_kind = classify_process_id(
        logical_process_id,
        self_reported_os_pid=report_pid,
        observed_os_pid=observed_pid,
        source=str(item_record.get("process_id_source") or ""),
    )
    return {
        "status": status,
        "required_evidence": list(required),
        "evidence": {name: _bool(value) for name, value in evidence.items()},
        "evidence_values": evidence,
        "command_match": _bool(command_match),
        "observed_pid_match": _bool(pid_match),
        "process_id_kind": process_id_kind.value,
        "logical_process_id": logical_process_id,
        "self_reported_os_pid": report_pid,
        "observed_os_pid": observed_pid,
        "reason": (
            "all required independent evidence matched"
            if status == "PASS"
            else "contradictory command or identity evidence"
            if status == "FAIL"
            else "one or more required evidence fields were not observable"
        ),
    }


def redact_identity(value: Any, *, project_root: Optional[Path] = None) -> Any:
    """Remove absolute local roots while retaining identity relationships."""

    replacements = []
    if project_root is not None:
        root = str(project_root.resolve())
        replacements.extend([root, "/private" + root if not root.startswith("/private/") else root])
    if isinstance(value, dict):
        return {str(key): redact_identity(item, project_root=project_root) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_identity(item, project_root=project_root) for item in value]
    if isinstance(value, str):
        for candidate in sorted(set(replacements), key=len, reverse=True):
            value = value.replace(candidate, "<project-root>")
        return value
    return value


__all__ = [
    "InvalidNativeTransition",
    "NativeState",
    "NativeStateMachine",
    "ProcessIdKind",
    "classify_process_id",
    "correlate_probe_evidence",
    "evidence_status",
    "redact_identity",
]
