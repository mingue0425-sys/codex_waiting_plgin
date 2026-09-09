#!/usr/bin/env python3
from __future__ import annotations

"""Aggregate v0.3 evidence without collapsing independent runtime objects."""

import json
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.3"
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


def status_from(value: Dict[str, Any], key: str = "status") -> str:
    raw = str(value.get(key, "UNKNOWN"))
    return raw if raw in VALID else "UNKNOWN"


def build() -> Dict[str, Any]:
    desktop = load("desktop-attach.json")
    schema = load("schema-probe.json")
    e2e = load("app-server-e2e.json")
    native = load("native-backend.json")
    races = load("handoff-races.json")
    crash = load("crash-reconnect.json")
    security = load("security-parity.json")
    benchmark = load("token-benchmark.json")
    crash_thread = crash.get("durable_thread_reconnect", {})
    crash_server = crash.get("app_server_crash", {})
    token_telemetry = bool(benchmark.get("telemetry_available")) or bool(
        (e2e.get("event_summary") or {}).get("notification_method_counts", {}).get("thread/tokenUsage/updated")
    )
    router_pass = (races.get("checks") or {}).get("same_event_routes_once", {}).get("status") == "PASS"
    capabilities = [
        {"name": "DESKTOP_LIVE_ATTACH", "status": status_from(desktop), "evidence": "desktop-attach.json"},
        {"name": "EXTERNAL_DESKTOP_ATTACH", "status": status_from(desktop), "evidence": "desktop-attach.json"},
        {"name": "DESKTOP_UI_REFLECTION", "status": "UNKNOWN", "evidence": "No external UI acknowledgement surface."},
        {"name": "DURABLE_THREAD_RESUME", "status": status_from(crash_thread), "evidence": "crash-reconnect.json"},
        {"name": "LIVE_THREAD_CONTINUATION", "status": "PASS" if e2e.get("same_thread_continuation") is True else "UNKNOWN", "evidence": "app-server-e2e.json"},
        {"name": "SNOOZE_OWNED_APP_SERVER", "status": "PASS" if all(status_from(item) == "PASS" for item in (e2e, security, crash)) else "PARTIAL" if status_from(native) == "PASS" and status_from(schema) == "PASS" else "UNKNOWN", "evidence": "app-server-e2e.json, security-parity.json, crash-reconnect.json"},
        {"name": "APP_SERVER_INITIALIZE", "status": status_from(schema), "evidence": "schema-probe.json and owned process trace"},
        {"name": "NATIVE_PROCESS_SPAWN", "status": status_from(native), "evidence": "native-backend.json"},
        {"name": "TURN_HANDOFF", "status": "PASS" if e2e.get("ten_second_handoff") is True else "UNKNOWN", "evidence": "app-server-e2e.json"},
        {"name": "LONG_JOB_SURVIVAL", "status": "PASS" if native.get("status") == "PASS" and e2e.get("job_survives_handoff") is True else "UNKNOWN", "evidence": "native-backend.json and app-server-e2e.json"},
        {"name": "COMPLETION_CONTINUATION", "status": "PASS" if e2e.get("same_thread_continuation") is True else "PARTIAL" if router_pass else "UNKNOWN", "evidence": "app-server-e2e.json and handoff-races.json"},
        {"name": "BUSY_THREAD_DELIVERY", "status": "PARTIAL", "evidence": "v0.2 concurrency-tests.json"},
        {"name": "APP_SERVER_CRASH_DETECTION", "status": status_from(crash_server), "evidence": "crash-reconnect.json"},
        {"name": "JOB_SURVIVES_APP_SERVER_LOSS", "status": str(crash_server.get("job_survives_app_server_loss", "UNKNOWN")), "evidence": "crash-reconnect.json"},
        {"name": "SANDBOX_PARITY", "status": status_from(security, "sandbox_parity"), "evidence": "security-parity.json"},
        {"name": "APPROVAL_PARITY", "status": status_from(security, "approval_parity"), "evidence": "security-parity.json"},
        {"name": "TOKEN_TELEMETRY", "status": "PASS" if token_telemetry else "UNKNOWN", "evidence": "token-benchmark.json/app-server-e2e.json"},
        {"name": "TOKEN_SAVINGS", "status": "UNKNOWN", "evidence": "No savings inference."},
        {"name": "AUTO_RESUME", "status": "UNKNOWN", "evidence": "Automatic Desktop resume is disabled."},
        {"name": "AUTO_CONTINUATION", "status": "PASS" if e2e.get("same_thread_continuation") is True else "UNKNOWN", "evidence": "app-server-e2e.json"},
        {"name": "AUTO_PRETOOL_INTERCEPTION", "status": "FAIL", "evidence": "The hook remains pass-through by design."},
    ]
    flags = {item["name"]: item["status"] for item in capabilities}
    safe = all(flags[name] == "PASS" for name in ("LIVE_THREAD_CONTINUATION", "LONG_JOB_SURVIVAL", "SANDBOX_PARITY", "APPROVAL_PARITY", "COMPLETION_CONTINUATION"))
    return {
        "schema_version": 1,
        "generated_at": e2e.get("generated_at") or schema.get("generated_at"),
        "control_plane": "SNOOZE_APP_SERVER" if safe else "APP_SERVER_PARTIAL",
        "selected_integration_mode": "SNOOZE_APP_SERVER" if safe else "CLI_RESUME_FALLBACK",
        "owned_app_server_status": flags["SNOOZE_OWNED_APP_SERVER"],
        "automatic_features_enabled": [],
        "capabilities": capabilities,
        "integration_flags": {name: flags[name] for name in (
            "DESKTOP_LIVE_ATTACH", "DURABLE_THREAD_RESUME", "LIVE_THREAD_CONTINUATION",
            "SNOOZE_OWNED_APP_SERVER", "TURN_HANDOFF", "LONG_JOB_SURVIVAL",
            "COMPLETION_CONTINUATION", "BUSY_THREAD_DELIVERY", "SANDBOX_PARITY",
            "APPROVAL_PARITY", "TOKEN_TELEMETRY", "AUTO_RESUME", "AUTO_CONTINUATION",
            "AUTO_PRETOOL_INTERCEPTION",
        )},
        "evidence_files": [
            "desktop-attach.json", "schema-probe.json", "app-server-e2e.json",
            "native-backend.json", "handoff-races.json", "crash-reconnect.json",
            "security-parity.json", "token-benchmark.json",
        ],
        "notes": [
            "Durable thread history, live core runtime, Desktop UI and owned App Server are separate objects.",
            "The model-backed handoff fixture generated terminal events but no durable result, so automatic continuation remains disabled.",
            "The experimental process/spawn fixture passed for an explicit Python process; sandbox and approval parity remain separate gates.",
            "Exactly-once is not claimed because the installed protocol exposes no client idempotency key.",
        ],
    }


def markdown(value: Dict[str, Any]) -> str:
    flags = value["integration_flags"]
    lines = [
        "# Codex Snooze v0.3 capabilities", "", f"Generated: `{value['generated_at']}`", "",
        "```text",
        f"CONTROL PLANE = {value['control_plane']}",
        f"DESKTOP ATTACH = {flags['DESKTOP_LIVE_ATTACH']}",
        f"SNOOZE-OWNED APP SERVER = {flags['SNOOZE_OWNED_APP_SERVER']}",
        f"10S HANDOFF = {flags['TURN_HANDOFF']}",
        f"AUTO CONTINUATION = {flags['AUTO_CONTINUATION']}",
        f"SANDBOX PARITY = {flags['SANDBOX_PARITY']}",
        f"APPROVAL PARITY = {flags['APPROVAL_PARITY']}",
        "AUTO PRETOOL INTERCEPTION = FAIL", "```", "",
        "| Capability | Status | Evidence |", "|---|---|---|",
    ]
    lines.extend(f"| `{item['name']}` | **{item['status']}** | `{item['evidence']}` |" for item in value["capabilities"])
    lines.extend([
        "", f"Selected integration mode: **`{value['selected_integration_mode']}`**", "",
        "No automatic features are enabled. The owned App Server path is an",
        "explicit experimental controller route until live handoff and parity",
        "evidence are repeatable.", "",
    ])
    lines.extend(f"- {item}" for item in value["notes"])
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
