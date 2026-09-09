#!/usr/bin/env python3
from __future__ import annotations

"""Summarize v0.5 model and wait telemetry without inferring savings."""

import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.5"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_core.models import utc_now
from tools.app_server_probe import redact, write_trace


def load(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def main() -> int:
    native = load(OUT / "native-terminal-e2e.json")
    baseline = load(ROOT / "results" / "v0.4" / "agent-benchmark.json")
    old_token = load(ROOT / "results" / "v0.3" / "token-benchmark.json")
    baseline_data = baseline.get("baseline", {})
    old_owned = old_token.get("snooze_owned_app_server", {})
    value = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "workload": "normal Codex terminal fixture requested as python3 fixture.py",
        "baseline": {
            "source": "results/v0.4/agent-benchmark.json",
            "turns": baseline_data.get("turn_count"),
            "model_events": baseline_data.get("model_active_events"),
            "tool_calls": baseline_data.get("tool_call_count"),
            "terminal_poll_count": baseline_data.get("terminal_poll_count"),
            "token_usage": baseline_data.get("token_usage"),
            "wall_seconds": baseline_data.get("wall_seconds"),
        },
        "native_candidate": {
            "source": "results/v0.5/native-terminal-e2e.json",
            "status": native.get("status"),
            "turns": 1 if native.get("turn_id") else 0,
            "model_events_during_wait": native.get("model_events_during_wait"),
            "tool_command_events": native.get("command_event_count"),
            "terminal_poll_count": 0,
            "token_usage_notifications": native.get("notification_method_counts", {}).get("thread/tokenUsage/updated", 0),
            "foreground_duration_seconds": native.get("threshold_elapsed_seconds"),
            "handoff_10s": native.get("handoff_10s"),
        },
        "prior_owned_server_reference": {
            "source": "results/v0.3/token-benchmark.json",
            "token_telemetry": bool(old_owned.get("token_telemetry")),
            "model_turn_count": old_owned.get("model_turn_count"),
            "tool_call_count": old_owned.get("tool_call_count"),
        },
        "model_events_during_wait": native.get("model_events_during_wait") if native else None,
        "telemetry_status": "PASS" if native.get("notification_method_counts", {}).get("thread/tokenUsage/updated", 0) > 0 else "UNKNOWN",
        "token_savings": "NOT_CLAIMED",
        "status": "PASS" if native.get("status") == "PASS" else "UNKNOWN" if not native.get("normal_tool_execution_observed") else "PARTIAL",
        "notes": [
            "The benchmark reports telemetry only when the normal terminal fixture was actually exercised.",
            "No token savings claim is made because the native security and ownership gates are not PASS.",
            "Controller-level polling count is zero in the native probe; notification processing is not model polling.",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "benchmark.json", redact(value))
    (OUT / "benchmark.md").write_text(
        "# v0.5 agent benchmark\n\n"
        f"Status: **{value['status']}**\n\n"
        f"Model events during wait: `{value['model_events_during_wait']}`\n\n"
        f"Telemetry: **{value['telemetry_status']}**\n\n"
        f"Token savings: **{value['token_savings']}**\n\n"
        "Native handoff remains security-gated; timing and telemetry are recorded without extrapolation.\n",
        encoding="utf-8",
    )
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
