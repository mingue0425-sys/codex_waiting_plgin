#!/usr/bin/env python3
from __future__ import annotations

"""Aggregate v0.4 explicit-handoff evidence conservatively."""

import json
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.4"
VALID = {"PASS", "PARTIAL", "FAIL", "UNKNOWN"}


def load(name: str) -> Dict[str, Any]:
    path = OUT / name
    if not path.exists():
        return {"status": "UNKNOWN", "missing": name}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"status": "UNKNOWN", "invalid": name}
    except (OSError, ValueError):
        return {"status": "UNKNOWN", "invalid": name}


def status(value: Dict[str, Any], key: str = "status") -> str:
    raw = str(value.get(key, "UNKNOWN"))
    return raw if raw in VALID else "UNKNOWN"


def first(value: Dict[str, Any]) -> Dict[str, Any]:
    results = value.get("results")
    return results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else {}


def build() -> Dict[str, Any]:
    handoff = load("handoff-e2e.json")
    failure = load("failure-e2e.json")
    stale = load("stale-e2e.json")
    repetitions = load("handoff-repetitions.json")
    thresholds = load("threshold-tests.json")
    shell = load("shell-handoff.json")
    races = load("continuation-races.json")
    app_crash = load("app-server-crash.json")
    controller_crash = load("controller-crash.json")
    security = load("security-parity.json")
    backend = load("backend-differential.json")
    benchmark = load("agent-benchmark.json")
    primary = first(handoff)
    primary_app = ((primary.get("app_server_snapshot") or {}).get("app_server") or {})
    primary_handoff_ok = status(handoff) == "PASS" and primary.get("explicit_handoff") is True
    model_idle = "PASS" if primary.get("model_events_during_long_wait") == 0 and primary.get("no_model_activity_during_wait") is True else "UNKNOWN"
    token_telemetry = "PASS" if primary.get("token_usage_notifications_during_wait", 0) > 0 or bool((benchmark.get("baseline") or {}).get("token_usage")) else "UNKNOWN"
    flags = {
        "DESKTOP_ATTACH": "FAIL",
        "AUTO_PRETOOL_INTERCEPTION": "FAIL",
        "SNOOZE_OWNED_APP_SERVER": "PASS" if primary_handoff_ok and primary_app.get("experimental_api") is True else "PARTIAL",
        "EXPLICIT_HANDOFF": "PASS" if primary_handoff_ok else status(handoff),
        "10S_HANDOFF": "PASS" if primary.get("ten_second_handoff") is True else "UNKNOWN",
        "JOB_SURVIVES_TURN_END": "PASS" if primary.get("job_survives_turn_end") is True else "UNKNOWN",
        "MODEL_IDLE_DURING_WAIT": model_idle,
        "AUTO_CONTINUATION": "PASS" if primary.get("same_thread_continuation") is True and primary.get("final_contains_result_token") is True and primary.get("rerun_guard") is True else "UNKNOWN",
        "COMPLETION_EVENT": "PASS" if (primary.get("job_result") or {}).get("completion_event_id") else "UNKNOWN",
        "CONTINUATION_DEDUP": status(races),
        "APP_SERVER_CRASH_RECOVERY": status(app_crash),
        "CONTROLLER_CRASH_RECOVERY": status(controller_crash),
        "FAILURE_PROPAGATION": status(failure),
        "STALE_PROPAGATION": status(stale),
        "THRESHOLD_SEMANTICS": status(thresholds),
        "HANDOFF_REPETITIONS": status(repetitions),
        "ZSH_HANDOFF": next((item.get("detached_handoff", "UNKNOWN") for item in shell.get("results", []) if item.get("shell") == "/bin/zsh"), "UNKNOWN"),
        "BASH_HANDOFF": next((item.get("detached_handoff", "UNKNOWN") for item in shell.get("results", []) if item.get("shell") == "/bin/bash"), "UNKNOWN"),
        "SANDBOX_PARITY": status(security, "sandbox_parity"),
        "APPROVAL_PARITY": status(security, "approval_parity"),
        "NATIVE_BACKEND": status(backend),
        "TOKEN_TELEMETRY": token_telemetry,
        "TOKEN_SAVINGS": "UNKNOWN",
    }
    security_blocked = flags["SANDBOX_PARITY"] == "FAIL" or flags["APPROVAL_PARITY"] == "FAIL"
    core_ready = all(flags[name] == "PASS" for name in (
        "SNOOZE_OWNED_APP_SERVER",
        "EXPLICIT_HANDOFF",
        "10S_HANDOFF",
        "JOB_SURVIVES_TURN_END",
        "AUTO_CONTINUATION",
    ))
    return {
        "schema_version": 1,
        "generated_at": primary.get("generated_at") or handoff.get("generated_at"),
        "control_plane": "SNOOZE_APP_SERVER_EXPERIMENTAL" if core_ready else "APP_SERVER_PARTIAL",
        "selected_production_path": "CLI_RESUME_FALLBACK",
        "production_automation": "DISABLED_SECURITY_GATE" if security_blocked or not core_ready else "DISABLED_UNTIL_REVIEW",
        "automatic_features_enabled": [],
        "integration_flags": flags,
        "gates": {
            "core_handoff_chain": "PASS" if core_ready else "PARTIAL",
            "security_gate": "FAIL" if security_blocked else "PASS" if flags["SANDBOX_PARITY"] == flags["APPROVAL_PARITY"] == "PASS" else "UNKNOWN",
            "desktop_gate": "FAIL",
            "pretool_gate": "FAIL",
        },
        "evidence_files": [
            "handoff-e2e.json", "failure-e2e.json", "stale-e2e.json", "handoff-repetitions.json",
            "threshold-tests.json", "shell-handoff.json", "continuation-races.json", "app-server-crash.json",
            "controller-crash.json", "security-parity.json", "backend-differential.json", "agent-benchmark.json",
        ],
        "notes": [
            "Desktop attach and automatic PreToolUse interception remain fixed FAIL and were not re-researched.",
            "The model-backed explicit 10-second chain is separated from the sandbox/approval gate.",
            "The installed command/exec path allowed a temporary sibling write despite workspaceWrite/writableRoots; production automation is disabled.",
            "Completion delivery is at-least-once and exactly-once is not claimed.",
            "The native process/spawn backend remains experimental because its installed schema describes host execution without a Codex sandbox.",
        ],
    }


def markdown(value: Dict[str, Any]) -> str:
    flags = value["integration_flags"]
    lines = [
        "# Codex Snooze v0.4 capabilities", "", f"Generated: `{value['generated_at']}`", "",
        "```text",
        f"CONTROL PLANE = {value['control_plane']}",
        f"SNOOZE-OWNED APP SERVER = {flags['SNOOZE_OWNED_APP_SERVER']}",
        f"EXPLICIT HANDOFF = {flags['EXPLICIT_HANDOFF']}",
        f"10S HANDOFF = {flags['10S_HANDOFF']}",
        f"MODEL IDLE DURING WAIT = {flags['MODEL_IDLE_DURING_WAIT']}",
        f"AUTO CONTINUATION = {flags['AUTO_CONTINUATION']}",
        f"SANDBOX PARITY = {flags['SANDBOX_PARITY']}",
        f"APPROVAL PARITY = {flags['APPROVAL_PARITY']}",
        "DESKTOP ATTACH = FAIL",
        "AUTO PRETOOL INTERCEPTION = FAIL",
        f"PRODUCTION PATH = {value['selected_production_path']}",
        "```", "",
        "| Capability | Status |", "|---|---|",
    ]
    lines.extend(f"| `{name}` | **{result}** |" for name, result in flags.items())
    lines.extend(["", f"Production automation: **{value['production_automation']}**", ""])
    lines.extend(f"- {note}" for note in value["notes"])
    return "\n".join(lines) + "\n"


def main() -> int:
    value = build()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "capabilities.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "capabilities.md").write_text(markdown(value), encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
