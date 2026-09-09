#!/usr/bin/env python3
from __future__ import annotations

"""Probe the normal Codex terminal item lifecycle in a disposable thread.

This probe never calls ``command/exec``, ``process/spawn`` or
``thread/shellCommand``.  The model is asked to invoke the exact fixture
command through its normal terminal tool.  If the model does not do that, or
the installed runtime does not expose a handoff primitive, the relevant
capability remains UNKNOWN.
"""

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.5"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.agent import AgentController, AgentControllerError
from snooze_controller.app_server_process import AppServerProcess, ServerRequest
from snooze_controller.thread_registry import ThreadRegistry
from snooze_core.models import utc_now
from tools.app_server_probe import redact, write_trace
from probes.v0_5_support import compact_snapshot


EXPECTED_COMMAND = "python3 fixture.py"
HEARTBEAT_RE = re.compile(r"heartbeat=(\d+)")


def fixture_text() -> str:
    return '''from __future__ import annotations
import json
import os
import time
from pathlib import Path

root = Path(__file__).resolve().parent
state_path = root / "fixture-state.json"
try:
    state = json.loads(state_path.read_text(encoding="utf-8"))
except (OSError, ValueError):
    state = {"run_count": 0}
state["run_count"] = int(state.get("run_count", 0)) + 1
state["pid"] = os.getpid()
state["started_at"] = time.time()
state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
for index in range(32):
    (root / "heartbeat.log").open("a", encoding="utf-8").write(f"heartbeat={index} pid={os.getpid()}\\n")
    time.sleep(0.5)
state["completed"] = True
state["completed_at"] = time.time()
state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
print("V05_FIXTURE_COMPLETE", flush=True)
'''


class ApprovalRecorder:
    def __init__(self, decision: str = "decline") -> None:
        self.decision = decision
        self.requests: List[Dict[str, Any]] = []

    def __call__(self, request: ServerRequest) -> Optional[Dict[str, Any]]:
        params = request.params if isinstance(request.params, dict) else {}
        self.requests.append(
            {
                "id": request.request_id,
                "method": request.method,
                "params": params,
                "decision": self.decision if request.method == "item/commandExecution/requestApproval" else None,
            }
        )
        if request.method == "item/commandExecution/requestApproval":
            return {"decision": self.decision}
        return None


def _scrub(value: Any, root: Path) -> Any:
    variants = {str(root), str(root.resolve())}
    if str(root).startswith("/") and not str(root).startswith("/private/"):
        variants.add("/private" + str(root))
    if isinstance(value, dict):
        return {str(key): _scrub(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, root) for item in value]
    if isinstance(value, str):
        for candidate in sorted(variants, key=len, reverse=True):
            value = value.replace(candidate, "<v05-fixture-root>")
        return value.replace(str(ROOT), "<project-root>")
    return value


def _notification_counts(notifications: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in notifications:
        method = str(item.get("method"))
        counts[method] = counts.get(method, 0) + 1
    return counts


def _command_events(notifications: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    values: List[Dict[str, Any]] = []
    for notification in notifications:
        if notification.get("method") not in {"item/started", "item/completed", "item/commandExecution/outputDelta"}:
            continue
        params = notification.get("params") or {}
        item = params.get("item") if isinstance(params, dict) else None
        if isinstance(item, dict) and (
            item.get("type") == "commandExecution"
            or "command" in item
            or "processId" in item
        ):
            values.append(
                {
                    "method": notification.get("method"),
                    "item": item,
                    "item_id": item.get("id") or item.get("itemId"),
                    "command": item.get("command"),
                    "process_id": item.get("processId"),
                    "status": item.get("status"),
                    "exit_code": item.get("exitCode"),
                }
            )
    return values


def _heartbeat(root: Path) -> Dict[str, Any]:
    state: Dict[str, Any] = {}
    try:
        state = json.loads((root / "fixture-state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    count = 0
    try:
        lines = (root / "heartbeat.log").read_text(encoding="utf-8").splitlines()
        for line in lines:
            match = HEARTBEAT_RE.search(line)
            if match:
                count = max(count, int(match.group(1)) + 1)
    except OSError:
        pass
    return {"state": state, "heartbeat_count": count, "state_exists": bool(state)}


def _model_events_after(process: AppServerProcess, start_ns: int) -> List[Dict[str, Any]]:
    values = []
    for event in process.events:
        if event.get("monotonic_ns", 0) <= start_ns:
            continue
        if event.get("kind") != "notification":
            continue
        method = str(event.get("method"))
        if method.startswith("item/reasoning/") or method.startswith("item/agentMessage/"):
            values.append({"method": method, "monotonic_ns": event.get("monotonic_ns")})
    return values


def run_live() -> Dict[str, Any]:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v05-native-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        (workspace / "fixture.py").write_text(fixture_text(), encoding="utf-8")
        registry = ThreadRegistry(root / "thread-registry.json")
        approvals = ApprovalRecorder("decline")
        # The installed background-terminal listing is experimental.  This
        # only enables protocol discovery; it does not use command/exec or
        # process/spawn and does not widen the thread sandbox.
        process = AppServerProcess(cwd=workspace, request_handler=approvals, experimental_api=True)
        controller = AgentController(registry, cwd=workspace, process=process)
        first_turn_id: Optional[str] = None
        first_turn: Optional[Dict[str, Any]] = None
        interrupt_response: Optional[Dict[str, Any]] = None
        background_before: Optional[Dict[str, Any]] = None
        background_at_threshold: Optional[Dict[str, Any]] = None
        command_running_at_threshold = False
        error: Optional[str] = None
        turn_start_monotonic = time.monotonic()
        threshold_monotonic = turn_start_monotonic + 10.0
        threshold_ns = 0
        try:
            controller.start(timeout=30)
            controller.create_thread(
                cwd=workspace,
                sandbox="workspace-write",
                approval_policy="on-request",
                timeout=30,
            )
            background_before = controller.background_terminals(timeout=30)
            first_turn = controller.start_turn(
                "Execute exactly this command once:\n\npython3 fixture.py\n\nDo not replace it with another command.",
                timeout=30,
            )
            first_turn_id = str(first_turn["id"])
            while time.monotonic() < threshold_monotonic:
                if controller.completed_turn(first_turn_id) is not None:
                    break
                time.sleep(0.1)
            threshold_ns = time.monotonic_ns()
            try:
                background_at_threshold = controller.background_terminals(timeout=30)
            except (AgentControllerError, RuntimeError) as exc:
                background_at_threshold = {"error": f"{type(exc).__name__}: {exc}"}
            command_events_at_threshold = _command_events(process.notifications)
            command_running_at_threshold = bool(command_events_at_threshold) and not bool(
                any(item.get("method") == "item/completed" for item in command_events_at_threshold)
            )
            if controller.completed_turn(first_turn_id) is None:
                try:
                    interrupt_response = controller.interrupt_turn(first_turn_id, timeout=30)
                except (AgentControllerError, RuntimeError) as exc:
                    interrupt_response = {"error": f"{type(exc).__name__}: {exc}"}
            try:
                first_done = controller.wait_turn(first_turn_id, timeout=90)
            except (AgentControllerError, RuntimeError) as exc:
                first_done = {"error": f"{type(exc).__name__}: {exc}"}
            first_turn = first_done
        except (AgentControllerError, OSError, RuntimeError, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            first_turn = first_turn or {}
        heartbeat_at_threshold = _heartbeat(workspace)
        time.sleep(0.75)
        heartbeat_after_threshold = _heartbeat(workspace)
        notifications = process.notifications
        events = process.events
        command_events = _command_events(notifications)
        completed_command = any(item.get("method") == "item/completed" for item in command_events)
        command_values = [item.get("command") for item in command_events if item.get("command")]
        command_event = command_events[0] if command_events else {}
        background_items = (background_at_threshold or {}).get("data", []) if isinstance(background_at_threshold, dict) else []
        native_handle = any(
            isinstance(item, dict)
            and (item.get("processId") or item.get("itemId")) == command_event.get("process_id")
            for item in background_items
        ) if command_event.get("process_id") else False
        model_events = _model_events_after(process, threshold_ns) if threshold_ns else []
        turn_status = first_turn.get("status") if isinstance(first_turn, dict) else None
        command_observed = command_event.get("command")
        integrity_status = (
            "PASS"
            if command_observed == EXPECTED_COMMAND
            else "UNKNOWN"
            if command_observed is None
            else "PARTIAL"
        )
        live_command = heartbeat_at_threshold.get("state_exists") or bool(command_events)
        value: Dict[str, Any] = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "live_requested": True,
            "status": "UNKNOWN",
            "backend": "THREAD_NATIVE_TERMINAL",
            "thread_id": controller.thread_id,
            "turn_id": first_turn_id,
            "requested_command": EXPECTED_COMMAND,
            "command_events": command_events,
            "command_event_count": len(command_events),
            "command_values": command_values,
            "approval_requests": approvals.requests,
            "approval_request_count": len(approvals.requests),
            "notification_method_counts": _notification_counts(notifications),
            "background_before": background_before,
            "background_at_threshold": background_at_threshold,
            "background_item_count_at_threshold": len(background_items),
            "native_process_handle_observed": native_handle,
            "command_running_at_threshold": command_running_at_threshold,
            "threshold_elapsed_seconds": round(time.monotonic() - turn_start_monotonic, 3),
            "turn_status_after_threshold": turn_status,
            "interrupt_response": interrupt_response,
            "heartbeat_at_threshold": heartbeat_at_threshold,
            "heartbeat_after_threshold": heartbeat_after_threshold,
            "heartbeat_continued_after_threshold": heartbeat_after_threshold.get("heartbeat_count", 0) > heartbeat_at_threshold.get("heartbeat_count", 0),
            "command_completed_event": completed_command,
            "model_events_during_wait": len(model_events),
            "model_events_after_threshold": model_events,
            "command_integrity": integrity_status,
            "normal_tool_execution_observed": live_command,
            "sandbox_parity": "UNKNOWN",
            "approval_parity": "UNKNOWN",
            "handoff_10s": "UNKNOWN",
            "job_survival": "UNKNOWN",
            "model_idle": "PASS" if live_command and not model_events else "UNKNOWN",
            "completion_detection": "PASS" if completed_command else "UNKNOWN",
            "auto_continuation": "UNKNOWN",
            "same_thread_continuation": "UNKNOWN",
            "run_count": heartbeat_after_threshold.get("state", {}).get("run_count"),
            "error": error,
            "app_server_snapshot": compact_snapshot(process.snapshot()),
            "duration_seconds": round(time.monotonic() - started, 3),
        }
        if (
            value["normal_tool_execution_observed"]
            and value["native_process_handle_observed"]
            and value["command_running_at_threshold"]
            and value["heartbeat_continued_after_threshold"]
            and value["command_integrity"] == "PASS"
        ):
            value["handoff_10s"] = "PASS"
            value["job_survival"] = "PASS"
        if value["handoff_10s"] == "PASS" and value["sandbox_parity"] == value["approval_parity"] == "PASS":
            value["status"] = "PASS"
        elif value["normal_tool_execution_observed"]:
            value["status"] = "PARTIAL"
        process.stop()
        return _scrub(redact(value), root)


def markdown(value: Dict[str, Any]) -> str:
    lines = [
        "# v0.5 normal Codex terminal lifecycle",
        "",
        f"Status: **{value.get('status')}**",
        "",
        "The fixture was requested as the exact normal terminal command `python3 fixture.py`.",
        "The controller did not call `command/exec`, `process/spawn`, or `thread/shellCommand`.",
        "",
        "| Evidence | Status/value |",
        "|---|---|",
    ]
    for key in (
        "normal_tool_execution_observed",
        "command_event_count",
        "approval_request_count",
        "native_process_handle_observed",
        "command_running_at_threshold",
        "heartbeat_continued_after_threshold",
        "command_completed_event",
        "model_events_during_wait",
        "command_integrity",
        "sandbox_parity",
        "approval_parity",
        "handoff_10s",
        "job_survival",
        "completion_detection",
        "auto_continuation",
        "run_count",
    ):
        lines.append(f"| `{key}` | `{value.get(key)}` |")
    lines.extend(
        [
            "",
            "A normal command item and a process identifier are insufficient to claim background ownership.",
            "The installed runtime must also prove process survival, sandbox parity, approval parity and completion ownership.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    value = run_live()
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "native-terminal-e2e.json", value)
    (OUT / "native-terminal-e2e.md").write_text(markdown(value), encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
