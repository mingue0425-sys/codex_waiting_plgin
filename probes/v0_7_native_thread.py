#!/usr/bin/env python3
from __future__ import annotations

"""Live v0.7 observation through one normal Codex App Server thread.

The controller creates a fixture and observes the runtime. The candidate
command is requested only in the model turn using the normal terminal tool.
This module never calls command/exec, process/spawn, or thread/shellCommand
for the candidate and never starts a supervisor.
"""

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.7"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from probes.v0_6_support import ScopedApprovalRecorder
from snooze_controller.agent import AgentController, AgentControllerError
from snooze_controller.app_server_process import AppServerProcess
from snooze_controller.v07_provenance import (
    NativeState,
    NativeStateMachine,
    correlate_probe_evidence,
    redact_identity,
)
from snooze_core.models import utc_now
from tools.app_server_probe import redact, write_trace


FORBIDDEN_CONTROLLER_METHODS = {"command/exec", "process/spawn", "thread/shellCommand"}
_JSON_OBJECT_RE = re.compile(r"\{[^{}]{0,5000}\}")


def _json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _first_probe_record(text: str, nonce: str) -> Optional[Dict[str, Any]]:
    for match in _JSON_OBJECT_RE.finditer(text):
        try:
            value = json.loads(match.group(0))
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("nonce") == nonce and "pid" in value:
            return value
    return None


def _output_strings(value: Any, *, key: Optional[str] = None) -> Iterable[str]:
    if isinstance(value, dict):
        for child_key, child in value.items():
            if child_key in {"delta", "aggregatedOutput", "formattedOutput", "output", "stdout", "stderr"}:
                yield from _output_strings(child, key=child_key)
            elif child_key not in {"command", "cwd", "argv", "input", "text_elements"}:
                yield from _output_strings(child, key=child_key)
    elif isinstance(value, list):
        for child in value:
            yield from _output_strings(child, key=key)
    elif isinstance(value, str) and key in {"delta", "aggregatedOutput", "formattedOutput", "output", "stdout", "stderr"}:
        yield value


def _item(notification: Mapping[str, Any]) -> Dict[str, Any]:
    params = notification.get("params")
    if not isinstance(params, Mapping):
        return {}
    value = params.get("item")
    if isinstance(value, Mapping):
        item = dict(value)
    else:
        item = dict(params)
    return {
        "item_id": item.get("id") or item.get("itemId") or params.get("itemId"),
        "type": item.get("type") or item.get("itemType"),
        "command": item.get("command"),
        "cwd": item.get("cwd"),
        "process_id": item.get("processId") or item.get("process_id"),
        "os_pid": item.get("osPid") or item.get("os_pid"),
        "exit_code": item.get("exitCode"),
        "status": item.get("status"),
        "process_id_source": "thread/backgroundTerminals",
    }


def command_notifications(notifications: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    values: List[Dict[str, Any]] = []
    for notification in notifications:
        method = str(notification.get("method") or "")
        if method not in {"item/started", "item/completed", "item/commandExecution/outputDelta"}:
            continue
        item = _item(notification)
        if item.get("type") == "commandExecution" or item.get("command") is not None or item.get("process_id") is not None:
            values.append({"method": method, "item": item, "params": notification.get("params")})
    return values


def inspect_pid(pid: Any, expected_command: str) -> Dict[str, Any]:
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return {"status": "UNKNOWN", "reason": "pid unavailable"}
    if value <= 0:
        return {"status": "UNKNOWN", "reason": "pid was non-positive"}
    try:
        result = subprocess.run(
            ["ps", "-o", "pid=,ppid=,lstart=,command=", "-p", str(value)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "UNKNOWN", "reason": f"ps unavailable: {type(exc).__name__}"}
    line = result.stdout.strip()
    if result.returncode != 0 or not line:
        return {"status": "UNKNOWN", "pid": value, "alive": False, "returncode": result.returncode}
    return {
        "status": "OBSERVED",
        "pid": value,
        "alive": True,
        "raw_command": line[:600],
        "command_match": Path(expected_command.split()[1]).name in line if len(expected_command.split()) > 1 else None,
        "start_identity_match": None,
    }


def _background(controller: AgentController) -> Dict[str, Any]:
    try:
        return controller.background_terminals(timeout=20)
    except (AgentControllerError, RuntimeError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def run_once(*, duration: float = 20.0, exit_code: int = 0, interrupt_at: float = 10.0) -> Dict[str, Any]:
    run_id = secrets.token_hex(8)
    run_root = ROOT / "runtime_probe_runs" / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    nonce = f"SNOOZE_PROBE_{secrets.token_hex(16)}"
    marker = run_root / "markers" / f"{nonce}.json"
    result_path = run_root / "result.json"
    run_count = run_root / "run_count.txt"
    heartbeat_path = run_root / "heartbeat.json"
    script = (ROOT / "fixtures" / "native_yield_probe.py").resolve()
    command = (
        f"python3 {script} --nonce {nonce} --marker {marker} --result {result_path} "
        f"--run-count {run_count} --duration {duration:.3f} --exit-code {exit_code}"
    )
    expected_token = f"RESULT_TOKEN={hashlib.sha256(nonce.encode('utf-8')).hexdigest()[:24]}"
    approvals = ScopedApprovalRecorder("decline", expected_fragment=script.name)
    process = AppServerProcess(cwd=run_root, experimental_api=True, request_handler=approvals)
    controller = AgentController(process=process, registry=__import__("snooze_controller.thread_registry", fromlist=["ThreadRegistry"]).ThreadRegistry(run_root / "thread-registry.json"), cwd=run_root)
    state = NativeStateMachine()
    timeline: List[Dict[str, Any]] = []
    t0 = time.monotonic()

    def mark(name: str, **fields: Any) -> None:
        timeline.append({"name": name, "monotonic_ns": time.monotonic_ns(), "elapsed_seconds": time.monotonic() - t0, **fields})

    turn_id: Optional[str] = None
    turn_payload: Dict[str, Any] = {}
    threshold_background: Dict[str, Any] = {}
    before_background: Dict[str, Any] = {}
    after_background: Dict[str, Any] = {}
    interrupt_response: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    turn_completed_before_threshold = False
    interrupt_requested = False
    provenance: Dict[str, Any] = {"status": "UNKNOWN", "reason": "not evaluated"}
    try:
        mark("turn_started")
        controller.start(timeout=40)
        controller.create_thread(cwd=run_root, sandbox="workspace-write", approval_policy="on-request", timeout=40)
        before_background = _background(controller)
        prompt = (
            "Use the normal terminal execution tool and execute exactly this raw command once.\n"
            f"{command}\n"
            "Do not replace it, simulate it, run it through a Snooze wrapper, or execute another command. "
            "If the normal terminal tool exposes yield_time_ms, set yield_time_ms=10000. "
            "Do not use command/exec, process/spawn, thread/shellCommand, or any wait/write_stdin polling tool."
        )
        turn_payload = controller.start_turn(prompt, timeout=40)
        turn_id = str(turn_payload.get("id"))
        state.transition(NativeState.COMMAND_ITEM_STARTED)
        state.transition(NativeState.PROVENANCE_PENDING)
        mark("command_item_requested", turn_id=turn_id)
        deadline = time.monotonic() + max(1.0, interrupt_at)
        while time.monotonic() < deadline:
            if turn_id and controller.completed_turn(turn_id) is not None:
                turn_completed_before_threshold = True
                break
            time.sleep(0.1)
        mark("threshold_reached", completed_before_threshold=turn_completed_before_threshold)
        threshold_background = _background(controller)
        notifications = process.notifications
        records = command_notifications(notifications)
        started = next((value for value in records if value["method"] == "item/started"), records[0] if records else {"item": {}})
        output_text = "\n".join(text for notification in notifications for text in _output_strings(notification.get("params")))
        self_report = _first_probe_record(output_text, nonce)
        marker_record = _json(marker)
        expected_cwd = str(run_root.resolve())
        os_observation = inspect_pid(self_report.get("pid") if self_report else None, command)
        if self_report:
            os_observation["cwd"] = self_report.get("cwd") if os_observation.get("alive") else None
        provenance = correlate_probe_evidence(
            expected_nonce=nonce,
            item_record=started.get("item", {}),
            output_text=output_text,
            marker_record=marker_record,
            self_report=self_report,
            os_observation=os_observation,
            expected_cwd=expected_cwd,
            expected_command=None,
            logical_process_id=started.get("item", {}).get("process_id"),
        )
        if provenance.get("status") == "PASS":
            state.transition(NativeState.PROVENANCE_ESTABLISHED, provenance_established=True)
            state.transition(NativeState.FOREGROUND_RUNNING, provenance_established=True)
            mark("process_identity_observed", provenance_status=provenance["status"])
        else:
            mark("process_identity_not_established", provenance_status=provenance.get("status"))
        if not turn_completed_before_threshold and provenance.get("status") == "PASS":
            state.transition(NativeState.YIELDED, provenance_established=True)
            state.transition(NativeState.HANDOFF_PENDING, provenance_established=True)
            mark("yield_boundary_candidate", requested=True)
            try:
                interrupt_response = controller.interrupt_turn(turn_id, timeout=20)
                interrupt_requested = True
                mark("turn_interrupt_request", accepted=True)
                state.transition(NativeState.TURN_CLOSED, provenance_established=True)
                # Controller-side observation only. This does not issue a model
                # turn or a wait/write_stdin tool call.
                state.transition(NativeState.BACKGROUND_RUNNING, provenance_established=True)
                mark("background_observation_started")
            except (AgentControllerError, RuntimeError) as exc:
                mark("turn_interrupt_request", accepted=False, error=f"{type(exc).__name__}: {exc}")
        else:
            mark("yield_boundary_not_proven", reason="turn completed early or provenance was not established")
        if turn_id and controller.completed_turn(turn_id) is None:
            try:
                turn_payload = controller.wait_turn(turn_id, timeout=max(45.0, duration + 30.0))
            except (AgentControllerError, RuntimeError) as exc:
                error = f"{type(exc).__name__}: {exc}"
        else:
            turn_payload = controller.completed_turn(turn_id) or turn_payload
        mark("turn_completed", status=turn_payload.get("status"))
        after_background = _background(controller)
    except (AgentControllerError, OSError, RuntimeError, ValueError) as exc:
        error = f"{type(exc).__name__}: {exc}"
        mark("probe_error", error=error)
    finally:
        snapshot = process.snapshot()
        controller.process.stop()

    marker_record = _json(marker)
    result_record = _json(result_path)
    heartbeat_record = _json(heartbeat_path)
    try:
        run_count_value = int(run_count.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        run_count_value = None
    notifications = snapshot.get("notifications", [])
    records = command_notifications(notifications)
    output_text = "\n".join(text for notification in notifications for text in _output_strings(notification.get("params")))
    self_report = _first_probe_record(output_text, nonce)
    forbidden = [
        event.get("method")
        for event in snapshot.get("events", [])
        if event.get("kind") == "request" and event.get("method") in FORBIDDEN_CONTROLLER_METHODS
    ]
    completed_item = next((value["item"] for value in records if value["method"] == "item/completed"), {})
    final_item = next((value["item"] for value in records if value["method"] == "item/started"), completed_item)
    final_marker = _json(marker)
    final_os_observation = inspect_pid(self_report.get("pid") if self_report else None, command)
    if final_item and (provenance.get("status") != "PASS" or not provenance.get("evidence_values")):
        provenance = correlate_probe_evidence(
            expected_nonce=nonce,
            item_record=final_item,
            output_text=output_text,
            marker_record=final_marker,
            self_report=self_report,
            os_observation=final_os_observation,
            expected_cwd=str(run_root.resolve()),
            expected_command=None,
            logical_process_id=final_item.get("process_id"),
        )
    native_yield = "PASS" if interrupt_requested and any(item["name"] == "background_observation_started" for item in timeline) else "FAIL" if (turn_payload.get("status") == "completed" and not interrupt_requested) or error is not None else "UNKNOWN"
    survival = "PASS" if interrupt_requested and heartbeat_record is not None and result_record is not None else "UNKNOWN"
    completion = "PASS" if result_record is not None and completed_item.get("exit_code") in {0, exit_code} and provenance.get("status") == "PASS" else "UNKNOWN"
    model_events_after_threshold = [
        {"method": event.get("method"), "monotonic_ns": event.get("monotonic_ns")}
        for event in snapshot.get("events", [])
        if event.get("kind") == "notification"
        and event.get("monotonic_ns", 0) > next((item["monotonic_ns"] for item in timeline if item["name"] == "threshold_reached"), 0)
        and str(event.get("method", "")).startswith(("item/reasoning/", "item/agentMessage/", "item/commandExecution/"))
    ]
    wait_family = [
        event.get("method")
        for event in snapshot.get("events", [])
        if event.get("kind") == "request" and event.get("method") in {"wait", "write_stdin", "status"}
    ]
    value: Dict[str, Any] = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "candidate": "THREAD_NATIVE_TERMINAL",
        "normal_tool_execution_only": True,
        "controller_candidate_execution_used": False,
        "forbidden_controller_methods": forbidden,
        "fixture": {
            "probe_version": 1,
            "nonce": nonce,
            "command": command,
            "expected_command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest(),
            "expected_cwd": str(run_root.resolve()),
            "duration_seconds": duration,
            "requested_exit_code": exit_code,
            "expected_result_token": expected_token,
        },
        "execution_namespace": {
            "controller_workspace": str(run_root.resolve()),
            "app_server_cwd": snapshot.get("cwd"),
            "fixture_script": str(script),
            "marker_path": str(marker),
            "marker_observed": marker_record is not None,
            "result_observed": result_record is not None,
            "run_count": run_count_value,
            "same_absolute_namespace": snapshot.get("cwd") == str(run_root.resolve()),
        },
        "command_records": records,
        "item_completed": completed_item,
        "stdout_evidence": output_text[-12000:],
        "self_report": self_report,
        "marker_record": marker_record,
        "result_record": result_record,
        "heartbeat_record": heartbeat_record,
        "os_observation": inspect_pid(self_report.get("pid") if self_report else None, command),
        "provenance": provenance,
        "native_yield": native_yield,
        "job_survival": survival,
        "completion_detection": completion,
        "turn_completed_before_threshold": turn_completed_before_threshold,
        "interrupt_requested": interrupt_requested,
        "interrupt_response": interrupt_response,
        "turn": turn_payload,
        "error": error,
        "timeline": timeline,
        "state_machine": {"state": state.state.value, "history": [item.value for item in state.history]},
        "background_registry": {"before": before_background, "threshold": threshold_background, "after": after_background},
        "model_events_after_threshold": model_events_after_threshold,
        "wait_family_calls_during_wait": wait_family,
        "continuation": {
            "status": "NOT_RUN",
            "reason": "continuation is forbidden until native yield, survival, completion and exit-code correlation are all PASS",
        },
        "status": "PASS" if native_yield == "PASS" and survival == "PASS" and completion == "PASS" and not forbidden else "UNKNOWN",
        "production_selection": "CLI_RESUME_FALLBACK",
    }
    value = redact_identity(redact(value), project_root=ROOT)
    # The run directory is disposable. It is removed only after the App
    # Server connection has been closed and all evidence has been read.
    shutil.rmtree(run_root, ignore_errors=True)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--interrupt-at", type=float, default=10.0)
    args = parser.parse_args()
    value = run_once(duration=args.duration, exit_code=args.exit_code, interrupt_at=args.interrupt_at)
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "native-live-observation.json", value)
    namespace_observation = value["execution_namespace"]
    write_trace(OUT / "execution-namespace.json", {"schema_version": 1, "generated_at": utc_now(), "observation": namespace_observation, "status": "PASS" if namespace_observation["same_absolute_namespace"] and namespace_observation["marker_observed"] and namespace_observation["result_observed"] else "UNKNOWN", "candidate_side_effect": "PASS" if namespace_observation["marker_observed"] else "FAIL"})
    write_trace(OUT / "process-provenance.json", {"schema_version": 1, "generated_at": utc_now(), "status": value["provenance"]["status"], "observation": value["provenance"], "self_report": value["self_report"], "marker": value["marker_record"], "os_observation": value["os_observation"]})
    write_trace(OUT / "native-yield.json", {"schema_version": 1, "generated_at": utc_now(), "status": value["native_yield"], "job_survival": value["job_survival"], "background_registry": value["background_registry"], "interrupt_requested": value["interrupt_requested"], "reason": value["error"] or "live normal-thread observation"})
    write_trace(OUT / "native-timeline.json", {"schema_version": 1, "generated_at": utc_now(), "status": value["status"], "timeline": value["timeline"], "state_machine": value["state_machine"], "wait_family_calls_during_wait": value["wait_family_calls_during_wait"], "model_events_after_threshold": value["model_events_after_threshold"]})
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
