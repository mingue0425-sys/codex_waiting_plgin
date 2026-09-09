from __future__ import annotations

"""Bounded helpers for v0.6 live ownership probes.

The live harness writes fixtures only.  It never invokes a candidate command
from the controller and never calls command/exec, process/spawn or
thread/shellCommand.  ``ps`` is used only as bounded observation of a PID
already exposed by normal-thread evidence.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
HEARTBEAT_RE = re.compile(r"heartbeat=(\d+)")

from snooze_controller.agent import AgentController, AgentControllerError
from snooze_controller.app_server_process import AppServerProcess, ServerRequest
from snooze_controller.v06_ownership import command_integrity_v06, correlate_process_records
from snooze_controller.thread_registry import ThreadRegistry
from snooze_core.models import utc_now
from tools.app_server_probe import redact
from probes.v0_5_support import compact_snapshot


def fixture_text(candidate: str, duration_seconds: int = 16) -> str:
    if candidate == "THREAD_NATIVE_TERMINAL":
        return f'''from __future__ import annotations
import json
import os
import time
from pathlib import Path

root = Path(__file__).resolve().parent
state_path = root / "fixture-state.json"
state = {{"run_count": 0}}
try:
    state.update(json.loads(state_path.read_text(encoding="utf-8")))
except (OSError, ValueError):
    pass
state["run_count"] = int(state.get("run_count", 0)) + 1
state["pid"] = os.getpid()
state["started_monotonic"] = time.monotonic()
state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
for index in range({duration_seconds * 2}):
    with (root / "heartbeat.log").open("a", encoding="utf-8") as handle:
        handle.write(f"heartbeat={{index}} pid={{os.getpid()}}\\n")
    time.sleep(0.5)
state["completed"] = True
state["completed_monotonic"] = time.monotonic()
state_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
print("V06_NATIVE_FIXTURE_COMPLETE", flush=True)
'''
    if candidate == "SANDBOX_DESCENDANT_SUPERVISOR":
        return f'''from __future__ import annotations
import json
import os
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parent
state_path = root / "supervisor-state.json"
role = sys.argv[1]
def write(name, value):
    (root / name).write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
if role == "parent":
    child = subprocess.Popen([sys.executable, __file__, "child"], cwd=root)
    value = {{"role": "parent", "pid": os.getpid(), "ppid": os.getppid(), "child_pid": child.pid}}
    child.wait(timeout=10)
    value["child_returncode"] = child.returncode
    write("parent-state.json", value)
    print(json.dumps(value), flush=True)
elif role == "child":
    worker = subprocess.Popen([sys.executable, __file__, "worker"], cwd=root, start_new_session=True)
    value = {{"role": "child", "pid": os.getpid(), "ppid": os.getppid(), "worker_pid": worker.pid}}
    write("child-state.json", value)
    print(json.dumps(value), flush=True)
elif role == "worker":
    value = {{"role": "worker", "pid": os.getpid(), "ppid": os.getppid(), "started_monotonic": time.monotonic(), "run_count": 1}}
    write("worker-state.json", value)
    for index in range({duration_seconds * 2}):
        with (root / "worker-heartbeat.log").open("a", encoding="utf-8") as handle:
            handle.write(f"heartbeat={{index}} pid={{os.getpid()}}\\n")
        time.sleep(0.5)
    value["completed"] = True
    value["completed_monotonic"] = time.monotonic()
    write("worker-state.json", value)
    print("V06_SUPERVISOR_WORKER_COMPLETE", flush=True)
else:
    raise SystemExit("unknown role")
'''
    raise ValueError(f"unknown candidate: {candidate}")


class ScopedApprovalRecorder:
    """Explicit approval policy scoped to one benign fixture fragment."""

    def __init__(self, decision: str = "decline", expected_fragment: str = "") -> None:
        if decision not in {"decline", "accept", "acceptForSession"}:
            raise ValueError("unsupported approval decision")
        self.decision = decision
        self.expected_fragment = expected_fragment
        self.requests: List[Dict[str, Any]] = []

    def __call__(self, request: ServerRequest) -> Optional[Dict[str, Any]]:
        params = request.params if isinstance(request.params, dict) else {}
        encoded = json.dumps(params, ensure_ascii=False, sort_keys=True)
        matched = bool(self.expected_fragment) and self.expected_fragment in encoded
        value = {
            "id": request.request_id,
            "method": request.method,
            "expected_fragment_matched": matched,
            "decision": None,
        }
        if request.method == "item/commandExecution/requestApproval":
            value["decision"] = self.decision if matched else "decline"
            self.requests.append({**value, "params": params})
            return {"decision": value["decision"]}
        self.requests.append(value)
        return None


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def read_fixture_state(root: Path, candidate: str) -> Dict[str, Any]:
    names = ["fixture-state.json"] if candidate == "THREAD_NATIVE_TERMINAL" else [
        "parent-state.json", "child-state.json", "worker-state.json"
    ]
    values = {name.removesuffix("-state.json"): _read_json(root / name) for name in names}
    heartbeat_name = "heartbeat.log" if candidate == "THREAD_NATIVE_TERMINAL" else "worker-heartbeat.log"
    count = 0
    try:
        for line in (root / heartbeat_name).read_text(encoding="utf-8").splitlines():
            match = HEARTBEAT_RE.search(line)
            if match:
                count = max(count, int(match.group(1)) + 1)
    except OSError:
        pass
    return {"records": values, "heartbeat_count": count, "state_exists": any(value is not None for value in values.values())}


def _item_from_notification(notification: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    params = notification.get("params")
    if not isinstance(params, Mapping):
        return None
    item = params.get("item")
    return item if isinstance(item, Mapping) else None


def command_events(notifications: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    values: List[Dict[str, Any]] = []
    for notification in notifications:
        if notification.get("method") not in {"item/started", "item/completed", "item/commandExecution/outputDelta"}:
            continue
        item = _item_from_notification(notification)
        if not isinstance(item, Mapping):
            continue
        if item.get("type") != "commandExecution" and "command" not in item and "processId" not in item:
            continue
        values.append(
            {
                "method": notification.get("method"),
                "item_id": item.get("id") or item.get("itemId"),
                "command": item.get("command"),
                "cwd": item.get("cwd"),
                "process_id": item.get("processId"),
                "os_pid": item.get("osPid"),
                "source": item.get("source"),
                "status": item.get("status"),
                "exit_code": item.get("exitCode"),
            }
        )
    return values


def background_items(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, Mapping) or not isinstance(value.get("data"), list):
        return []
    return [dict(item) for item in value["data"] if isinstance(item, Mapping)]


def model_events_after(process: AppServerProcess, threshold_ns: int) -> List[Dict[str, Any]]:
    values = []
    for event in process.events:
        if event.get("monotonic_ns", 0) <= threshold_ns or event.get("kind") != "notification":
            continue
        method = str(event.get("method"))
        if method.startswith("item/reasoning/") or method.startswith("item/agentMessage/"):
            values.append({"method": method, "monotonic_ns": event.get("monotonic_ns")})
    return values


def inspect_pid(pid: Any) -> Dict[str, Any]:
    try:
        numeric = int(pid)
    except (TypeError, ValueError):
        return {"status": "UNKNOWN", "reason": "pid unavailable"}
    if numeric <= 0:
        return {"status": "UNKNOWN", "reason": "pid was non-positive"}
    try:
        result = subprocess.run(
            ["ps", "-o", "pid=,ppid=,pgid=,lstart=,command=", "-p", str(numeric)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "UNKNOWN", "reason": f"ps unavailable: {type(exc).__name__}"}
    line = result.stdout.strip()
    if result.returncode != 0 or not line:
        return {"status": "UNKNOWN", "pid": numeric, "alive": False, "returncode": result.returncode}
    return {"status": "OBSERVED", "pid": numeric, "alive": True, "ps": line[:500]}


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
            value = value.replace(candidate, "<v06-fixture-root>")
        return value.replace(str(ROOT), "<project-root>")
    return value


def run_normal_probe(candidate: str, *, run_id: int = 1, threshold_seconds: float = 10.0) -> Dict[str, Any]:
    """Run one bounded normal-thread observation for A or B."""

    if candidate not in {"THREAD_NATIVE_TERMINAL", "SANDBOX_DESCENDANT_SUPERVISOR"}:
        raise ValueError("only candidate A or B may be probed")
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"codex-snooze-v06-{run_id}-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        fixture_name = "fixture.py" if candidate == "THREAD_NATIVE_TERMINAL" else "supervisor_fixture.py"
        fixture_path = workspace / fixture_name
        fixture_path.write_text(fixture_text(candidate), encoding="utf-8")
        registry = ThreadRegistry(root / "thread-registry.json")
        approvals = ScopedApprovalRecorder("decline", expected_fragment=fixture_name)
        process = AppServerProcess(cwd=workspace, request_handler=approvals, experimental_api=True)
        controller = AgentController(registry, cwd=workspace, process=process)
        prompt_command = f"python3 {fixture_name}" if candidate == "THREAD_NATIVE_TERMINAL" else f"python3 {fixture_name} parent"
        prompt = (
            "Use the normal terminal tool and execute exactly this command once. "
            f"Do not simulate it, replace it, wrap it with a Snooze command, or run another command: {prompt_command}"
        )
        turn: Dict[str, Any] = {}
        turn_id: Optional[str] = None
        first_background: Any = None
        threshold_background: Any = None
        final_background: Any = None
        error: Optional[str] = None
        threshold_ns = 0
        completed_before_threshold = False
        turn_started_at = time.monotonic()
        threshold_at = turn_started_at + threshold_seconds
        try:
            controller.start(timeout=30)
            controller.create_thread(cwd=workspace, sandbox="workspace-write", approval_policy="on-request", timeout=30)
            try:
                first_background = controller.background_terminals(timeout=30)
            except (AgentControllerError, RuntimeError) as exc:
                first_background = {"error": f"{type(exc).__name__}: {exc}"}
            turn = controller.start_turn(prompt, timeout=30)
            turn_id = str(turn["id"])
            while time.monotonic() < threshold_at:
                if controller.completed_turn(turn_id) is not None:
                    completed_before_threshold = True
                    break
                time.sleep(0.1)
            threshold_ns = time.monotonic_ns()
            try:
                threshold_background = controller.background_terminals(timeout=30)
            except (AgentControllerError, RuntimeError) as exc:
                threshold_background = {"error": f"{type(exc).__name__}: {exc}"}
            try:
                turn = controller.wait_turn(turn_id, timeout=120)
            except (AgentControllerError, RuntimeError) as exc:
                error = f"{type(exc).__name__}: {exc}"
            try:
                final_background = controller.background_terminals(timeout=30)
            except (AgentControllerError, RuntimeError) as exc:
                final_background = {"error": f"{type(exc).__name__}: {exc}"}
        except (AgentControllerError, OSError, RuntimeError, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        raw_snapshot = process.snapshot()
        process.stop()
        state_at_end = read_fixture_state(workspace, candidate)
        notifications = raw_snapshot.get("notifications", [])
        events = command_events(notifications)
        b_items = background_items(threshold_background)
        event = next((item for item in events if item.get("method") == "item/started"), events[0] if events else {})
        correlations = [correlate_process_records(event, item).as_dict() for item in b_items]
        best_correlation = next((item for item in correlations if item.get("status") == "PASS"), correlations[0] if correlations else None)
        command_integrity = command_integrity_v06(
            prompt_command,
            event_command=event.get("command"),
            background_command=(b_items[0].get("command") if b_items else None),
            requested_cwd=str(workspace),
            event_cwd=event.get("cwd"),
            background_cwd=(b_items[0].get("cwd") if b_items else None),
            event_source=event.get("source"),
        )
        pids = []
        for record in list(state_at_end.get("records", {}).values()) + events + b_items:
            if isinstance(record, Mapping):
                for key in ("pid", "os_pid", "osPid", "process_id", "processId", "worker_pid", "child_pid"):
                    if record.get(key) is not None:
                        pids.append(record.get(key))
        process_observations = {str(pid): inspect_pid(pid) for pid in dict.fromkeys(pids)}
        heartbeat_before = read_fixture_state(workspace, candidate)
        time.sleep(0.75)
        heartbeat_after = read_fixture_state(workspace, candidate)
        model_events = model_events_after(process, threshold_ns) if threshold_ns else []
        completed_event = next((item for item in reversed(events) if item.get("method") == "item/completed"), None)
        turn_completed = bool(turn.get("status")) and str(turn.get("status")) not in {"inProgress", "started"}
        normal_observed = bool(events) or bool(state_at_end.get("state_exists"))
        native_handle = best_correlation is not None and best_correlation.get("status") == "PASS"
        heartbeat_continued = heartbeat_after.get("heartbeat_count", 0) > heartbeat_before.get("heartbeat_count", 0)
        value: Dict[str, Any] = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "candidate": candidate,
            "run_id": run_id,
            "requested_command": prompt_command,
            "requested_cwd": str(workspace),
            "thread_id": controller.thread_id,
            "turn_id": turn_id,
            "turn": turn,
            "normal_tool_execution_observed": normal_observed,
            "fixture_exercised": bool(state_at_end.get("state_exists")),
            "command_events": events,
            "background_before": first_background,
            "background_at_threshold": threshold_background,
            "background_final": final_background,
            "background_items_at_threshold": b_items,
            "process_correlations": correlations,
            "best_process_correlation": best_correlation,
            "process_observations": process_observations,
            "approval_requests": approvals.requests,
            "approval_request_count": len(approvals.requests),
            "threshold_elapsed_seconds": round(time.monotonic() - turn_started_at, 3),
            "threshold_seconds": threshold_seconds,
            "turn_completed_before_threshold": completed_before_threshold,
            "heartbeat_before_final_wait": heartbeat_before,
            "heartbeat_after_final_wait": heartbeat_after,
            "heartbeat_continued_after_observation": heartbeat_continued,
            "model_events_during_wait": len(model_events),
            "model_events_after_threshold": model_events,
            "completion_event": completed_event,
            "completion_exit_code": completed_event.get("exit_code") if completed_event else None,
            "command_integrity_evidence": command_integrity,
            "process_identity": best_correlation.get("status") if best_correlation else "UNKNOWN",
            "handoff_10s": "PASS" if native_handle and heartbeat_continued and not turn_completed else "UNKNOWN",
            "job_survival": "PASS" if native_handle and heartbeat_continued and turn_completed else "UNKNOWN",
            "model_idle": "PASS" if native_handle and not model_events else "UNKNOWN",
            "completion_detection": "PASS" if native_handle and completed_event and completed_event.get("exit_code") is not None else "UNKNOWN",
            "auto_continuation": "UNKNOWN",
            "same_thread_continuation_attempted": False,
            "controller_candidate_execution_used": False,
            "forbidden_methods_used": [
                event.get("method") for event in raw_snapshot.get("events", [])
                if event.get("kind") == "request" and event.get("method") in {"command/exec", "process/spawn", "thread/shellCommand"}
            ],
            "error": error,
            "app_server_snapshot": compact_snapshot(raw_snapshot),
            "duration_seconds": round(time.monotonic() - started, 3),
        }
        if value["command_integrity_evidence"].get("status") == "PASS":
            value["command_integrity"] = "PASS"
        elif value["command_integrity_evidence"].get("status") == "FAIL":
            value["command_integrity"] = "FAIL"
        else:
            value["command_integrity"] = "UNKNOWN"
        value["status"] = "PASS" if all(value.get(key) == "PASS" for key in (
            "process_identity", "command_integrity", "handoff_10s", "job_survival", "model_idle", "completion_detection", "auto_continuation"
        )) else "UNKNOWN"
        return _scrub(redact(value), root)


__all__ = [
    "ScopedApprovalRecorder",
    "background_items",
    "command_events",
    "fixture_text",
    "inspect_pid",
    "model_events_after",
    "read_fixture_state",
    "run_normal_probe",
]
