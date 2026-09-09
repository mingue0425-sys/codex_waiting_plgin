#!/usr/bin/env python3
from __future__ import annotations

"""Live E2E for the Snooze-owned App Server control loop.

The model is explicitly asked to use the existing, detached Snooze launcher.
The controller waits on the durable result file at the OS level and starts a
continuation only after completion, so no polling turn is generated while the
long job runs.  If the model does not issue the requested terminal call, that
fact and the observed protocol events are retained as UNKNOWN.
"""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.3"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.agent import AgentController, AgentControllerError
from snooze_controller.thread_registry import ThreadRegistry
from snooze_core.models import utc_now
from tools.app_server_probe import redact, write_trace


def _copy_fixtures(workspace: Path) -> None:
    fixtures = workspace / "fixtures"
    fixtures.mkdir(parents=True, exist_ok=True)
    for name in ("long_job.py", "failure_job.py"):
        shutil.copy2(ROOT / "probes" / "fixtures" / name, fixtures / name)


def _job_dirs(store: Path) -> List[Path]:
    jobs = store / "jobs"
    return sorted(path for path in jobs.iterdir() if path.is_dir()) if jobs.exists() else []


def _wait_for_result(store: Path, timeout: float = 45.0) -> Optional[Dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for directory in _job_dirs(store):
            result_path = directory / "result.json"
            if result_path.exists():
                try:
                    return json.loads(result_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
        time.sleep(0.1)
    return None


def _scrub(value: Any, workspace: Path, store: Path) -> Any:
    if isinstance(value, dict):
        return {str(key): _scrub(item, workspace, store) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, workspace, store) for item in value]
    if isinstance(value, str):
        return value.replace(str(workspace), "<fixture-workspace>").replace(str(store), "<fixture-store>")
    return value


def _event_summary(events: List[Dict[str, Any]], notifications: List[Dict[str, Any]], workspace: Path, store: Path) -> Dict[str, Any]:
    method_counts: Dict[str, int] = {}
    item_types: List[str] = []
    commands: List[str] = []
    for item in notifications:
        method = str(item.get("method"))
        method_counts[method] = method_counts.get(method, 0) + 1
        params = item.get("params") or {}
        nested = params.get("item") if isinstance(params, dict) else None
        if isinstance(nested, dict):
            item_type = nested.get("type") or nested.get("itemType")
            if item_type:
                item_types.append(str(item_type))
            command = nested.get("command")
            if isinstance(command, str):
                commands.append(command)
    turn_requests = [
        event for event in events if event.get("kind") == "request" and event.get("method") == "turn/start"
    ]
    return {
        "notification_method_counts": method_counts,
        "item_types": sorted(set(item_types)),
        "command_event_count": len(commands),
        "commands": commands,
        "turn_start_request_count": len(turn_requests),
        "event_count": len(events),
        "notification_count": len(notifications),
        "redacted_events": _scrub(redact(events), workspace, store),
        "redacted_notifications": _scrub(redact(notifications), workspace, store),
    }


def run_live() -> Dict[str, Any]:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v03-e2e-") as temporary:
        root = Path(temporary)
        workspace = root / "fixture-repo"
        workspace.mkdir()
        _copy_fixtures(workspace)
        store = root / "snooze-store"
        marker = workspace / "long-job-marker.json"
        registry = ThreadRegistry(root / "thread-registry.json")
        trace_path = root / "events.json"
        controller = AgentController(registry, cwd=workspace)
        first_turn_id: Optional[str] = None
        first_turn: Optional[Dict[str, Any]] = None
        continuation_turn: Optional[Dict[str, Any]] = None
        result: Optional[Dict[str, Any]] = None
        error: Optional[str] = None
        try:
            controller.start(timeout=30)
            controller.create_thread(
                cwd=workspace,
                sandbox="workspace-write",
                # This is an explicit probe choice for a benign fixture; the
                # controller still rejects unsolicited approval server requests.
                approval_policy="never",
                timeout=30,
            )
            command = (
                f"{ROOT / 'scripts' / 'codex-snooze'} --store {store} submit"
                f" --cwd {workspace} --handoff-after 10"
                f" --command 'python3 fixtures/long_job.py --duration 12 --marker {marker.name} --exit-code 0'"
            )
            task = (
                "Use the terminal tool now. Run exactly this command from the current workspace: "
                + command
                + ". Do not simulate the output and do not run a different command. "
                "This is an explicit Codex Snooze handoff test: wait for the launcher acknowledgement, "
                "then briefly state that the job was handed off. Do not poll the fixture in another turn."
            )
            first_started = time.monotonic()
            first_turn_info = controller.start_turn(task, timeout=30)
            first_turn_id = str(first_turn_info["id"])
            first_turn = controller.wait_turn(first_turn_id, timeout=180)
            first_elapsed = time.monotonic() - first_started
            result = _wait_for_result(store, timeout=45)
            jobs_after_first = _job_dirs(store)
            if result is not None and result.get("execution_state") in {"COMPLETED", "COMPLETED_STALE"}:
                event_id = result.get("completion_event_id")
                continuation_task = (
                    "A Snooze completion event is ready. Read the existing result metadata at "
                    f"jobs/{jobs_after_first[0].name}/result.json if present, using the terminal only if needed. "
                    f"The completion event id is {event_id}. Do not rerun the completed command. "
                    "Report the recorded exit_code and execution_state in one sentence."
                )
                continuation_turn = controller.continue_turn(continuation_task, timeout=30, wait_timeout=180)
            else:
                continuation_task = None
            event_summary = _event_summary(controller.process.events, controller.process.notifications, workspace, store)
            tool_seen = any(
                name in {"item/started", "item/completed", "item/commandExecution/outputDelta", "command/exec/outputDelta"}
                for name in event_summary["notification_method_counts"]
            )
            same_thread = controller.thread_id is not None
            no_polling_between = event_summary["turn_start_request_count"] == (2 if continuation_turn else 1)
            survival = result is not None and bool(marker.exists()) and result.get("exit_code") == 0
            handoff = survival and first_elapsed >= 9.0 and len(jobs_after_first) == 1
            continuation = continuation_turn is not None and same_thread
            status = "PASS" if tool_seen and handoff and continuation and no_polling_between else "UNKNOWN"
            if error:
                status = "UNKNOWN"
            value = {
                "schema_version": 1,
                "generated_at": utc_now(),
                "status": status,
                "control_plane": "SNOOZE_APP_SERVER",
                "live_requested": True,
                "thread_id": controller.thread_id,
                "first_turn_id": first_turn_id,
                "first_turn": first_turn,
                "continuation_turn": continuation_turn,
                "first_turn_elapsed_seconds": round(first_elapsed, 3),
                "job_result": result,
                "job_survives_handoff": survival,
                "ten_second_handoff": handoff,
                "same_thread_continuation": continuation,
                "no_model_polling_during_wait": no_polling_between,
                "tool_execution_observed": tool_seen,
                "rerun_guard": len(jobs_after_first) == 1,
                "event_summary": event_summary,
                "app_server_snapshot": _scrub(redact(controller.process.snapshot()), workspace, store),
                "error": error,
                "duration_seconds": round(time.monotonic() - started, 3),
                "analysis": (
                    "The model issued a terminal event, the detached supervisor completed, and a second turn ran on the same owned thread."
                    if status == "PASS"
                    else "The live protocol trace did not prove every handoff condition; inspect tool events and model behavior before enabling automatic continuation."
                ),
            }
        except (AgentControllerError, OSError, ValueError, RuntimeError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            value = {
                "schema_version": 1,
                "generated_at": utc_now(),
                "status": "UNKNOWN",
                "control_plane": "SNOOZE_APP_SERVER",
                "live_requested": True,
                "thread_id": controller.thread_id,
                "first_turn_id": first_turn_id,
                "first_turn": first_turn,
                "continuation_turn": continuation_turn,
                "job_result": result,
                "error": error,
                "event_summary": _event_summary(controller.process.events, controller.process.notifications, workspace, store),
                "app_server_snapshot": _scrub(redact(controller.process.snapshot()), workspace, store),
                "duration_seconds": round(time.monotonic() - started, 3),
            }
        finally:
            controller.process.stop()
            trace = _scrub(redact(controller.process.snapshot()), workspace, store)
            write_trace(trace_path, trace)
            value["event_trace"] = _scrub(redact(trace), workspace, store)
            # The temporary trace is intentionally not copied outside the
            # result payload; it is already bounded and redacted there.
        return value


def markdown(value: Dict[str, Any]) -> str:
    lines = [
        "# v0.3 Snooze-owned App Server E2E",
        "",
        f"Generated: `{value['generated_at']}`",
        f"Status: **{value['status']}**",
        "",
        value.get("analysis", ""),
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for key in (
        "tool_execution_observed",
        "ten_second_handoff",
        "job_survives_handoff",
        "no_model_polling_during_wait",
        "same_thread_continuation",
        "rerun_guard",
    ):
        if key in value:
            lines.append(f"| `{key}` | `{value[key]}` |")
    lines.extend(
        [
            "",
            f"Thread: `{value.get('thread_id')}`",
            f"First turn: `{value.get('first_turn_id')}`",
            f"First-turn elapsed: `{value.get('first_turn_elapsed_seconds')}` seconds",
            f"Turn starts observed: `{(value.get('event_summary') or {}).get('turn_start_request_count')}`",
            "",
            "The model was required to issue the terminal call through the owned",
            "App Server. If it did not, the result stays UNKNOWN and the protocol",
            "event summary explains whether a tool item was observed.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    value = run_live()
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "app-server-e2e.json", value)
    (OUT / "app-server-e2e.md").write_text(markdown(value), encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
