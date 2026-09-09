from __future__ import annotations

"""Evidence gates for v0.6 native/background ownership.

This module contains only evidence evaluation and backend selection.  It never
starts a candidate command, asks the App Server to execute one, or changes a
sandbox/approval policy.  A missing observation is deliberately UNKNOWN.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from .v05_backend import V05Backend


V06_REQUIRED_GATES = (
    "status",
    "sandbox_parity",
    "approval_parity",
    "command_integrity",
    "process_identity",
    "handoff_10s",
    "job_survival",
    "model_idle",
    "completion_detection",
    "auto_continuation",
)

V06_GATE_NAMES = (
    "COMMAND_INTEGRITY",
    "SANDBOX_PARITY",
    "APPROVAL_PARITY",
    "PROCESS_IDENTITY",
    "10S_HANDOFF",
    "JOB_SURVIVAL",
    "MODEL_IDLE_DURING_WAIT",
    "COMPLETION_DETECTION",
    "AUTO_CONTINUATION",
)

_VALID = {"PASS", "PARTIAL", "FAIL", "UNKNOWN", "NOT_SUPPORTED"}
_SHELL_WRAPPER_RE = re.compile(r"(?:^|/)(?:zsh|bash)(?:\s+[^ ]+)*\s+-l?c(?:\s|$)")


def status(value: Any, default: str = "UNKNOWN") -> str:
    raw = str(value)
    return raw if raw in _VALID else default


def command_sha256(command: Optional[str]) -> Optional[str]:
    if command is None:
        return None
    if not isinstance(command, str) or not command:
        raise ValueError("command must be a non-empty string or None")
    return hashlib.sha256(command.encode("utf-8")).hexdigest()


def classify_command_wrapper(command: Optional[str], *, source: Optional[str] = None) -> str:
    """Classify text without treating a runtime wrapper as integrity proof.

    A normal Codex command item may expose the shell invocation used by the
    runtime rather than the semantic command in the user prompt.  The source
    and shape are retained as evidence, but classification never upgrades the
    integrity status.
    """

    if not command:
        return "MISSING"
    if source in {"unifiedExecStartup", "agent", "normal-thread"} and _SHELL_WRAPPER_RE.search(command):
        return "NORMAL_CODEX_RUNTIME_WRAPPER"
    if _SHELL_WRAPPER_RE.search(command):
        return "SHELL_WRAPPER_UNATTRIBUTED"
    return "RAW_OR_UNKNOWN"


def command_integrity_v06(
    requested_command: Optional[str],
    *,
    event_command: Optional[str],
    background_command: Optional[str],
    executed_command: Optional[str] = None,
    requested_cwd: Optional[str] = None,
    event_cwd: Optional[str] = None,
    background_cwd: Optional[str] = None,
    executed_cwd: Optional[str] = None,
    event_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Compare every available command/cwd value without semantic guessing."""

    values = {
        "requested_command_sha256": command_sha256(requested_command),
        "event_command_sha256": command_sha256(event_command),
        "background_command_sha256": command_sha256(background_command),
        "executed_command_sha256": command_sha256(executed_command),
        "requested_cwd": requested_cwd,
        "event_cwd": event_cwd,
        "background_cwd": background_cwd,
        "executed_cwd": executed_cwd,
        "event_command_wrapper": classify_command_wrapper(event_command, source=event_source),
        "approval_semantics_proven": False,
    }
    commands = [requested_command, event_command, background_command, executed_command]
    cwds = [requested_cwd, event_cwd, background_cwd, executed_cwd]
    if any(value is not None and not isinstance(value, str) for value in commands + cwds):
        values["status"] = "FAIL"
        values["reason"] = "command or cwd evidence had an invalid type"
        return values
    if requested_command is None or requested_cwd is None:
        values["status"] = "UNKNOWN"
        values["reason"] = "requested command/cwd was not recorded"
        return values
    present_commands = [value for value in commands if value is not None]
    present_cwds = [value for value in cwds if value is not None]
    command_mismatch = any(value != requested_command for value in present_commands)
    cwd_mismatch = any(_same_path(requested_cwd, value) is False for value in present_cwds)
    normal_wrapper_only = (
        event_command is not None
        and event_command != requested_command
        and values["event_command_wrapper"] == "NORMAL_CODEX_RUNTIME_WRAPPER"
        and all(value in {None, requested_command} for value in (background_command, executed_command))
    )
    if (command_mismatch and not normal_wrapper_only) or cwd_mismatch:
        values["status"] = "FAIL"
        values["reason"] = "command or cwd mutation observed"
    elif normal_wrapper_only:
        values["status"] = "UNKNOWN"
        values["reason"] = "normal Codex runtime wrapper was observed; semantic command was not exposed exactly"
    elif len(present_commands) != len(commands) or len(present_cwds) != len(cwds):
        values["status"] = "UNKNOWN"
        values["reason"] = "one or more command/cwd stages were not observable"
    else:
        values["status"] = "PASS"
        values["reason"] = "all recorded command and cwd values matched exactly"
    return values


def _field(record: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in record:
            return record.get(name)
    return None


def _command_item(record: Mapping[str, Any]) -> Mapping[str, Any]:
    item = record.get("item")
    return item if isinstance(item, Mapping) else record


def _same_path(left: Optional[str], right: Optional[str]) -> Optional[bool]:
    if left is None or right is None:
        return None
    try:
        return Path(str(left)).resolve() == Path(str(right)).resolve()
    except (OSError, RuntimeError):
        return str(left) == str(right)


@dataclass(frozen=True)
class ProcessCorrelation:
    status: str
    item_id_match: Optional[bool]
    process_id_match: Optional[bool]
    cwd_match: Optional[bool]
    command_match: Optional[bool]
    os_pid_match: Optional[bool]
    start_identity_match: Optional[bool]
    identity_method: str
    reason: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "item_id_match": self.item_id_match,
            "process_id_match": self.process_id_match,
            "cwd_match": self.cwd_match,
            "canonical_command_match": self.command_match,
            "os_pid_match": self.os_pid_match,
            "start_identity_match": self.start_identity_match,
            "identity_method": self.identity_method,
            "reason": self.reason,
        }


def correlate_process_records(
    command_event: Mapping[str, Any],
    background_record: Mapping[str, Any],
    *,
    process_identity: Optional[Mapping[str, Any]] = None,
) -> ProcessCorrelation:
    """Correlate normal item evidence with a background record strictly.

    The background list is not accepted merely because the command text is
    similar.  Required protocol identities must match, and the production
    gate additionally requires a PID or another bounded start identity.
    """

    event_item = _command_item(command_event)
    bg_item = _command_item(background_record)
    event_item_id = _field(command_event, "item_id", "itemId", "id") or _field(event_item, "id", "itemId")
    bg_item_id = _field(background_record, "item_id", "itemId", "id") or _field(bg_item, "itemId", "id")
    event_process_id = _field(command_event, "process_id", "processId") or _field(event_item, "processId")
    bg_process_id = _field(background_record, "process_id", "processId") or _field(bg_item, "processId")
    event_cwd = _field(command_event, "cwd") or _field(event_item, "cwd")
    bg_cwd = _field(background_record, "cwd") or _field(bg_item, "cwd")
    event_command = _field(command_event, "command") or _field(event_item, "command")
    bg_command = _field(background_record, "command") or _field(bg_item, "command")
    event_os_pid = _field(command_event, "os_pid", "osPid") or _field(event_item, "osPid")
    bg_os_pid = _field(background_record, "os_pid", "osPid") or _field(bg_item, "osPid")
    item_match = None if event_item_id is None or bg_item_id is None else str(event_item_id) == str(bg_item_id)
    process_match = None if event_process_id is None or bg_process_id is None else str(event_process_id) == str(bg_process_id)
    cwd_match = _same_path(event_cwd, bg_cwd)
    command_match = None if event_command is None or bg_command is None else event_command == bg_command

    identity = process_identity or {}
    identity_method = "none"
    os_pid_match = None
    start_identity_match = None
    if event_os_pid is not None or bg_os_pid is not None:
        identity_method = "osPid"
        os_pid_match = None if event_os_pid is None or bg_os_pid is None else int(event_os_pid) == int(bg_os_pid)
    if "start_identity" in identity:
        identity_method = "pid+start_identity"
        expected = identity.get("expected_start_identity")
        observed = identity.get("observed_start_identity")
        start_identity_match = None if expected is None or observed is None else expected == observed
    required = (item_match, process_match, cwd_match, command_match)
    if any(value is False for value in required):
        return ProcessCorrelation("FAIL", item_match, process_match, cwd_match, command_match, os_pid_match, start_identity_match, identity_method, "protocol identity mismatch")
    identity_ok = os_pid_match is True or start_identity_match is True
    identity_bad = os_pid_match is False or start_identity_match is False
    if identity_bad:
        return ProcessCorrelation("FAIL", item_match, process_match, cwd_match, command_match, os_pid_match, start_identity_match, identity_method, "OS process identity mismatch")
    if all(value is True for value in required) and identity_ok:
        return ProcessCorrelation("PASS", item_match, process_match, cwd_match, command_match, os_pid_match, start_identity_match, identity_method, "item/process/cwd/command and process identity matched")
    return ProcessCorrelation("UNKNOWN", item_match, process_match, cwd_match, command_match, os_pid_match, start_identity_match, identity_method, "one or more correlation fields or process identity were not observable")


def gate_ready(evidence: Mapping[str, Any], *, required: Iterable[str] = V06_REQUIRED_GATES) -> bool:
    return all(status(evidence.get(name)) == "PASS" for name in required)


def select_v06_backend(evidence: Mapping[str, Any]) -> V05Backend:
    """Return only a fully proven A/B backend; C is never selectable."""

    native = evidence.get("THREAD_NATIVE_TERMINAL", {}) or {}
    if gate_ready(native):
        return V05Backend.THREAD_NATIVE_TERMINAL
    descendant = evidence.get("SANDBOX_DESCENDANT_SUPERVISOR", {}) or {}
    if gate_ready(descendant):
        return V05Backend.DESCENDANT_SUPERVISOR
    return V05Backend.CLI_RESUME_FALLBACK


def capability_flags(matrix: Mapping[str, Mapping[str, Any]]) -> Dict[str, str]:
    native = matrix.get("THREAD_NATIVE_TERMINAL", {}) or {}
    descendant = matrix.get("SANDBOX_DESCENDANT_SUPERVISOR", {}) or {}
    selected = native if gate_ready(native) else descendant if gate_ready(descendant) else {}
    return {
        "THREAD_NATIVE_TERMINAL": status(native.get("status")),
        "DESCENDANT_SUPERVISOR": status(descendant.get("status")),
        "CONTROLLER_COMMAND_EXEC": "FAIL",
        "NATIVE_SANDBOX_PARITY": status(native.get("sandbox_parity")),
        "NATIVE_APPROVAL_PARITY": status(native.get("approval_parity")),
        "COMMAND_INTEGRITY": status(selected.get("command_integrity")),
        "PROCESS_IDENTITY": status(selected.get("process_identity")),
        "10S_HANDOFF": status(selected.get("handoff_10s")),
        "JOB_SURVIVAL": status(selected.get("job_survival")),
        "MODEL_IDLE_DURING_WAIT": status(selected.get("model_idle")),
        "COMPLETION_DETECTION": status(selected.get("completion_detection")),
        "AUTO_CONTINUATION": status(selected.get("auto_continuation")),
    }


__all__ = [
    "ProcessCorrelation",
    "V06_GATE_NAMES",
    "V06_REQUIRED_GATES",
    "capability_flags",
    "classify_command_wrapper",
    "command_integrity_v06",
    "command_sha256",
    "correlate_process_records",
    "gate_ready",
    "select_v06_backend",
    "status",
]
