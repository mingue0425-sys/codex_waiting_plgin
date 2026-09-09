#!/usr/bin/env python3
from __future__ import annotations

"""Compare a normal terminal workload with an explicit handoff workload."""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.4"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def normal_run(workspace: Path) -> Dict[str, Any]:
    prompt = (
        "Use the terminal tool exactly once to run this benign command and wait for it: "
        "python3 -c \"import time; time.sleep(0.7); print('BENCH_BASELINE')\". "
        "Do not rerun it or manually poll it. Report the command result."
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
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=150, check=False, env=os.environ.copy())
        events: List[Dict[str, Any]] = []
        for line in completed.stdout.splitlines():
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                events.append(value)
        return {
            "returncode": completed.returncode,
            "wall_seconds": round(time.monotonic() - started, 3),
            "turn_count": sum(item.get("type") == "turn.started" for item in events),
            "agent_message_count": sum(item.get("item", {}).get("type") == "agent_message" for item in events),
            "tool_call_count": sum(item.get("item", {}).get("type") in {"command_execution", "custom_tool_call"} for item in events),
            "terminal_poll_count": "UNKNOWN",
            "model_active_events": sum(item.get("type") in {"turn.started", "item.started", "item.completed"} for item in events),
            "token_usage": [item.get("usage") for item in events if item.get("usage")],
            "event_types": sorted({str(item.get("type")) for item in events}),
            "stdout_tail": completed.stdout[-3000:],
            "stderr_tail": completed.stderr[-1000:],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": 124, "error": f"{type(exc).__name__}: {exc}", "wall_seconds": round(time.monotonic() - started, 3)}


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v04-agent-benchmark-") as temporary:
        root = Path(temporary)
        baseline_workspace = root / "baseline"
        baseline_workspace.mkdir()
        baseline = normal_run(baseline_workspace)
        owned_command = [
            sys.executable,
            str(ROOT / "probes" / "v0_4_handoff_e2e.py"),
            "--live",
            "--runs",
            "1",
            "--threshold",
            "0.2",
            "--duration",
            "0.7",
            "--approval-policy",
            "never",
            "--output-stem",
            "agent-benchmark-handoff",
        ]
        started = time.monotonic()
        try:
            owned_process = subprocess.run(owned_command, cwd=ROOT, capture_output=True, text=True, timeout=180, check=False)
            owned = json.loads(owned_process.stdout) if owned_process.stdout.strip() else {"status": "UNKNOWN", "stderr": owned_process.stderr[-2000:]}
            owned_result = (owned.get("results") or [{}])[0]
            owned_app = (owned_result.get("app_server_snapshot") or {}).get("app_server") or {}
            owned = {
                "returncode": owned_process.returncode,
                "status": owned.get("status"),
                "wall_seconds": round(time.monotonic() - started, 3),
                "turn_count": sum(event.get("kind") == "request" and event.get("method") == "turn/start" for event in owned_app.get("events", [])),
                "agent_message_count": sum(event.get("method") == "item/agentMessage/delta" for event in owned_app.get("notifications", [])),
                "tool_call_count": sum(item.get("method") == "item/tool/call" for item in owned.get("results", [{}])[0].get("app_server_snapshot", {}).get("app_server", {}).get("server_requests", [])),
                "terminal_poll_count": 0,
                "model_events_during_long_wait": owned_result.get("model_events_during_long_wait"),
                "token_usage_notifications": owned_result.get("token_usage_notifications_during_wait"),
                "token_usage_available": bool(owned_result.get("token_usage_notifications_during_wait")),
                "continuation": owned_result.get("same_thread_continuation"),
                "stdout_tail": owned_process.stdout[-1000:],
                "stderr_tail": owned_process.stderr[-1000:],
            }
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            owned = {"returncode": 124, "status": "UNKNOWN", "error": f"{type(exc).__name__}: {exc}", "wall_seconds": round(time.monotonic() - started, 3)}
        value = {
            "schema_version": 1,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "baseline": baseline,
            "snooze_owned_app_server": owned,
            "model_events_during_long_wait": owned.get("model_events_during_long_wait", "UNKNOWN"),
            "target_model_events_during_long_wait": 0,
            "token_telemetry": "PASS" if owned.get("token_usage_available") else "UNKNOWN",
            "token_savings": "NOT_CLAIMED",
            "status": "PASS" if baseline.get("returncode") == 0 and owned.get("status") == "PASS" else "UNKNOWN",
            "notes": [
                "The baseline is a normal Codex terminal workload; the Snooze run is an explicit handoff workload.",
                "Wall time and token telemetry are reported without inferring token savings.",
                "A zero model-event interval is measured from owned App Server notifications, not guessed from wall time.",
            ],
        }
    OUT.mkdir(parents=True, exist_ok=True)
    from tools.app_server_probe import redact, write_trace

    value = redact(value)
    write_trace(OUT / "agent-benchmark.json", value)
    (OUT / "agent-benchmark.md").write_text(
        "# v0.4 agent/model benchmark\n\n"
        f"Status: **{value['status']}**\n\n"
        f"Model events during the Snooze long-job wait: **{value['model_events_during_long_wait']}** (target `0`)\n\n"
        f"Token telemetry: **{value['token_telemetry']}**; savings: **{value['token_savings']}**\n\n"
        "Exactly-once and token savings are not inferred from this benchmark.\n",
        encoding="utf-8",
    )
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
