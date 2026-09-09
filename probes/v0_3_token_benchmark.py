#!/usr/bin/env python3
from __future__ import annotations

"""Run the same small fixture through normal exec and owned App Server paths."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.3"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.agent import AgentController, AgentControllerError
from snooze_controller.app_server_process import AppServerProcess
from snooze_controller.thread_registry import ThreadRegistry
from tools.app_server_probe import redact, utc_now, write_trace


FIXTURE = ROOT / "probes" / "fixtures" / "long_job.py"


def _scrub(value: Any, root: Path) -> Any:
    if isinstance(value, dict):
        return {str(key): _scrub(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, root) for item in value]
    if isinstance(value, str):
        return value.replace(str(root), "<benchmark-fixture>")
    return value


def _token_payload(item: Any) -> Any:
    if isinstance(item, dict):
        for key in ("tokenUsage", "token_usage", "usage", "usageMetadata"):
            if key in item:
                return item[key]
        return {key: _token_payload(value) for key, value in item.items() if "token" in str(key).lower() or "usage" in str(key).lower()}
    if isinstance(item, list):
        return [_token_payload(value) for value in item]
    return None


def _normal(root: Path, workspace: Path) -> Dict[str, Any]:
    marker = workspace / "baseline-marker.json"
    prompt = (
        "Use the terminal tool exactly once to run this command and wait for it to finish: "
        f"python3 fixtures/long_job.py --duration 2 --marker {marker.name} --exit-code 0. "
        "Do not simulate the output."
    )
    command = [
        "codex",
        "exec",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "--config",
        'approval_policy="never"',
        "--cd",
        str(workspace),
        prompt,
    ]
    started = time.monotonic()
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=150, check=False, env=os.environ.copy())
        events = []
        for line in result.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        types = [str(item.get("type")) for item in events if isinstance(item, dict)]
        return {
            "returncode": result.returncode,
            "wall_seconds": round(time.monotonic() - started, 3),
            "marker_written": marker.exists(),
            "model_turn_count": sum(1 for value in types if "turn" in value and "started" in value),
            "model_request_count": sum(1 for value in types if value in {"turn.started", "turn_start"}),
            "tool_call_count": sum(1 for value in types if "item" in value and ("started" in value or "completed" in value)),
            "event_types": sorted(set(types)),
            "token_telemetry": [_token_payload(item) for item in events if _token_payload(item)],
            "stdout_tail": result.stdout[-4000:],
            "stderr_tail": result.stderr[-4000:],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": 124, "error": f"{type(exc).__name__}: {exc}", "wall_seconds": round(time.monotonic() - started, 3), "marker_written": marker.exists()}


def _owned(root: Path, workspace: Path) -> Dict[str, Any]:
    marker = workspace / "owned-marker.json"
    prompt = (
        "Use the terminal tool exactly once to run this command and wait for it to finish: "
        f"python3 fixtures/long_job.py --duration 2 --marker {marker.name} --exit-code 0. "
        "Do not simulate the output."
    )
    registry = ThreadRegistry(root / "owned-threads.json")
    process = AppServerProcess(cwd=ROOT)
    controller = AgentController(registry, process=process)
    started = time.monotonic()
    try:
        process.start(timeout=30)
        controller.create_thread(cwd=workspace, sandbox="workspace-write", approval_policy="never", timeout=30)
        turn = controller.start_turn(prompt, timeout=30)
        completed = controller.wait_turn(turn["id"], timeout=180)
        token_events = [item for item in process.notifications if item.get("method") == "thread/tokenUsage/updated"]
        method_names = [str(item.get("method")) for item in process.notifications]
        turn_start_count = sum(1 for item in process.events if item.get("kind") == "request" and item.get("method") == "turn/start")
        tool_count = sum(1 for item in process.notifications if item.get("method") in {"item/started", "item/completed"})
        return {
            "return_status": completed.get("status"),
            "wall_seconds": round(time.monotonic() - started, 3),
            "marker_written": marker.exists(),
            "model_turn_count": turn_start_count,
            "model_request_count": turn_start_count,
            "tool_call_count": tool_count,
            "notification_methods": sorted(set(method_names)),
            "token_telemetry": token_events,
            "server_requests": [item.method for item in process.server_requests],
        }
    except (OSError, RuntimeError, AgentControllerError) as exc:
        return {
            "error": f"{type(exc).__name__}: {exc}",
            "wall_seconds": round(time.monotonic() - started, 3),
            "marker_written": marker.exists(),
            "model_turn_count": sum(1 for item in process.events if item.get("kind") == "request" and item.get("method") == "turn/start"),
            "model_request_count": sum(1 for item in process.events if item.get("kind") == "request" and item.get("method") == "turn/start"),
            "tool_call_count": sum(1 for item in process.notifications if item.get("method") in {"item/started", "item/completed"}),
            "token_telemetry": [item for item in process.notifications if item.get("method") == "thread/tokenUsage/updated"],
        }
    finally:
        process.stop()


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v03-benchmark-") as temporary:
        root = Path(temporary)
        baseline_workspace = root / "baseline"
        owned_workspace = root / "owned"
        for workspace in (baseline_workspace, owned_workspace):
            workspace.mkdir()
            shutil.copy2(FIXTURE, workspace / "fixtures.py")
            # Keep the prompt's stable fixture name without copying a package.
            (workspace / "fixtures").mkdir()
            shutil.copy2(FIXTURE, workspace / "fixtures" / "long_job.py")
        baseline = _normal(root, baseline_workspace)
        owned = _owned(root, owned_workspace)
        telemetry_available = bool(baseline.get("token_telemetry") or owned.get("token_telemetry"))
        value = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "baseline": _scrub(redact(baseline), root),
            "snooze_owned_app_server": _scrub(redact(owned), root),
            "telemetry_available": telemetry_available,
            "comparison": {
                "model_turn_count": {
                    "baseline": baseline.get("model_turn_count"),
                    "snooze": owned.get("model_turn_count"),
                },
                "model_request_count": {
                    "baseline": baseline.get("model_request_count"),
                    "snooze": owned.get("model_request_count"),
                },
                "tool_call_count": {
                    "baseline": baseline.get("tool_call_count"),
                    "snooze": owned.get("tool_call_count"),
                },
                "poll_operations": {"baseline": "not observed", "snooze": 0},
                "wall_time_seconds": {"baseline": baseline.get("wall_seconds"), "snooze": owned.get("wall_seconds")},
            },
            "status": "PASS" if baseline.get("marker_written") and owned.get("marker_written") else "UNKNOWN",
            "token_savings_claim": "NOT_CLAIMED" if not telemetry_available else "NOT_INFERRED",
        }
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "token-benchmark.json", value)
    (OUT / "token-benchmark.md").write_text(
        "# v0.3 token/model request benchmark\n\n"
        f"Status: **{value['status']}**\n\n"
        "The benchmark runs the same benign two-second fixture through normal `codex exec` "
        "and a Snooze-owned App Server thread. Turn/request/tool counts come from protocol "
        "events where available. Token telemetry is reported only when the runtime exposes it; "
        "no token savings are inferred from wall time.\n",
        encoding="utf-8",
    )
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
