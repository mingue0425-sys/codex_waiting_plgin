#!/usr/bin/env python3
from __future__ import annotations

"""Aggregate v0.2 measurements into a conservative control-plane decision."""

import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.2"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.gate import choose_control_plane


VALID_STATUSES = {"PASS", "PARTIAL", "FAIL", "UNKNOWN"}


def read_value(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def read_json(path: Path, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    value = read_value(path, default or {})
    return value if isinstance(value, dict) else dict(default or {})


def status_values(values: Iterable[Any]) -> List[str]:
    return [str(value) for value in values if str(value) in VALID_STATUSES]


def mixed_status(values: Iterable[Any]) -> str:
    statuses = status_values(values)
    if not statuses:
        return "UNKNOWN"
    if "FAIL" in statuses:
        return "FAIL"
    if all(status == "PASS" for status in statuses):
        return "PASS"
    if "PASS" in statuses or "PARTIAL" in statuses:
        return "PARTIAL"
    return "UNKNOWN"


def _v01_status(payload: Dict[str, Any], name: str) -> str:
    for item in payload.get("capabilities", []):
        if isinstance(item, dict) and item.get("name") == name:
            status = item.get("status")
            return str(status) if status in VALID_STATUSES else "UNKNOWN"
    return "UNKNOWN"


def _v01_item(payload: Dict[str, Any], name: str) -> Dict[str, Any]:
    for item in payload.get("capabilities", []):
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return {}


def _history_statuses(history: Any, predicate: str) -> List[str]:
    values: List[str] = []
    if not isinstance(history, list):
        return values
    for item in history:
        if not isinstance(item, dict):
            continue
        observations = item.get("observations") or {}
        if predicate == "supervisor_survived" and observations.get("supervisor_survival") is True:
            values.append("PASS")
        elif predicate == "command_continued" and observations.get("marker_written") is True:
            values.append("PASS")
        elif predicate == "exit_authoritative" and observations.get("child_exit_code") == 0:
            values.append("PASS")
        else:
            overall = str(item.get("status", "UNKNOWN"))
            values.append(overall if overall in {"FAIL", "PARTIAL"} else "UNKNOWN")
    return values


def _app_server_control_status(app: Dict[str, Any], env: Dict[str, Any]) -> str:
    if env.get("status") == "FAIL":
        return "FAIL"
    capabilities = app.get("capabilities") or {}
    required = (
        capabilities.get("app_server_initialize"),
        capabilities.get("thread_list"),
        capabilities.get("thread_start_read_background_queue_list"),
    )
    if all(value == "PASS" for value in required):
        return "PASS"
    if any(value in {"PASS", "PARTIAL"} for value in required):
        return "PARTIAL"
    return "UNKNOWN"


def build_payload(base: Optional[Path] = None) -> Dict[str, Any]:
    base = base or OUT
    v01 = read_json(ROOT / "results" / "capabilities.json")
    history = read_value(ROOT / "results" / "snooze-e2e-history.json", [])
    app = read_json(base / "app-server-probes.json")
    env = read_json(base / "codex-environment.json")
    interrupt = read_json(base / "interrupt-tests.json")
    delivery = read_json(base / "delivery-races.json")
    concurrency = read_json(base / "concurrency-tests.json")
    security = read_json(base / "security-probes.json")
    stale = read_json(base / "project-stale-tests.json")

    v01_interrupt = _v01_status(v01, "turn_interrupt")
    v01_resume = _v01_status(v01, "codex_exec_resume")
    v01_history = _v01_item(v01, "codex_exec_resume").get("observations") or {}
    history_values = history if isinstance(history, list) else []
    survival = mixed_status(_history_statuses(history_values, "supervisor_survived"))
    command_continued = mixed_status(_history_statuses(history_values, "command_continued"))
    exit_authoritative = mixed_status(_history_statuses(history_values, "exit_authoritative"))
    race_status = str(interrupt.get("aggregate_status", "UNKNOWN"))
    if race_status not in VALID_STATUSES:
        race_status = "UNKNOWN"
    if race_status in {"FAIL", "PARTIAL"}:
        survival = "FAIL" if race_status == "FAIL" else "PARTIAL"
        command_continued = "FAIL" if race_status == "FAIL" else "PARTIAL"
        exit_authoritative = "FAIL" if race_status == "FAIL" else "PARTIAL"
    elif race_status == "UNKNOWN" and survival == "UNKNOWN":
        survival = "UNKNOWN"

    security_capabilities = security.get("capabilities") or {}
    concurrency_capabilities = concurrency.get("capabilities") or {}
    app_server_control = _app_server_control_status(app, env)
    flags: Dict[str, str] = {
        "app_server_control": app_server_control,
        "conversation_history_shared": "PASS"
        if v01_resume == "PASS" and v01_history.get("conversation_history_restored") is True
        else "UNKNOWN",
        "thread_identifier_shared": "PASS"
        if app.get("capabilities", {}).get("thread_start_read_background_queue_list") == "PASS"
        else "UNKNOWN",
        "desktop_thread_visible": "UNKNOWN",
        "desktop_ui_integration": "UNKNOWN",
        "cwd_restored": "UNKNOWN",
        "model_config_restored": "UNKNOWN",
        "sandbox_policy_restored": "UNKNOWN",
        "approval_policy_restored": "UNKNOWN",
        "tool_state_restored": "UNKNOWN",
        "cli_resume": v01_resume,
        "turn_interrupt": v01_interrupt,
        "job_survives_interrupt": survival,
        "actual_command_continues_after_interrupt": command_continued,
        "exit_code_authoritative_after_interrupt": exit_authoritative,
        "turn_handoff_supported": mixed_status((survival, command_continued, exit_authoritative, race_status)),
        "busy_thread_delivery": str(
            concurrency_capabilities.get("busy_thread_delivery", app.get("capabilities", {}).get("busy_thread_delivery", "UNKNOWN"))
        ),
        "idle_check_to_external_turn_race": str(
            concurrency_capabilities.get("idle_check_to_external_turn_race", "UNKNOWN")
        ),
        "automatic_completion_delivery": str(
            concurrency_capabilities.get("automatic_completion_delivery", "UNKNOWN")
        ),
        "delivery_deduplication_and_recovery": str(delivery.get("aggregate_status", "UNKNOWN")),
        "project_change_stale": str(stale.get("aggregate_status", "UNKNOWN")),
        "actual_command_integrity": str(security_capabilities.get("actual_command_integrity", "UNKNOWN")),
        "environment_value_persistence": str(
            security_capabilities.get("environment_value_persistence", "UNKNOWN")
        ),
        "sandbox_preserved": str(security_capabilities.get("sandbox_preserved", "UNKNOWN")),
        "approval_preserved": str(security_capabilities.get("approval_preserved", "UNKNOWN")),
        "network_boundary": str(security_capabilities.get("network_boundary", "UNKNOWN")),
        "auto_pretool_rewrite": str(security_capabilities.get("auto_pretool_rewrite", "UNKNOWN")),
        "auto_pretool_interception": str(
            security_capabilities.get("auto_pretool_interception", "UNKNOWN")
        ),
    }
    for name, value in list(flags.items()):
        if value not in VALID_STATUSES:
            flags[name] = "UNKNOWN"

    control_plane = choose_control_plane(flags)
    integration_flags = {
        "TURN_INTERRUPT_SUPPORTED": flags["turn_interrupt"],
        "JOB_SURVIVES_INTERRUPT": flags["job_survives_interrupt"],
        "BUSY_THREAD_DELIVERY_SUPPORTED": flags["busy_thread_delivery"],
        "DESKTOP_UI_INTEGRATION_SUPPORTED": flags["desktop_ui_integration"],
        "SANDBOX_PRESERVED": flags["sandbox_preserved"],
        "APPROVAL_PRESERVED": flags["approval_preserved"],
        "AUTO_RESUME_SUPPORTED": "UNKNOWN",
        "AUTO_HANDOFF_SUPPORTED": flags["turn_handoff_supported"],
        "AUTO_PRETOOL_INTERCEPTION_SUPPORTED": flags["auto_pretool_interception"],
    }
    capability_items = [
        {
            "name": name,
            "status": status,
            "evidence": _evidence_for(name, status, app, concurrency, interrupt, security, stale),
        }
        for name, status in flags.items()
    ]
    return {
        "schema_version": 2,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "codex_version": env.get("codex_version") or app.get("codex_version") or "UNKNOWN",
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "control_plane": control_plane,
        "feature_flags": flags,
        "integration_flags": integration_flags,
        "capabilities": capability_items,
        "automatic_features": {
            "auto_resume": flags["desktop_ui_integration"] == "PASS",
            "auto_handoff": False,
            "auto_pretool_interception": False,
            "reason": "all unproven control-plane and security gates remain disabled",
        },
        "sources": {
            "v01_capabilities": str(ROOT / "results" / "capabilities.json"),
            "app_server": str(base / "app-server-probes.json"),
            "interrupt": str(base / "interrupt-tests.json"),
            "delivery": str(base / "delivery-races.json"),
            "concurrency": str(base / "concurrency-tests.json"),
            "security": str(base / "security-probes.json"),
            "project_stale": str(base / "project-stale-tests.json"),
        },
        "decision_reasons": [
            "Installed App Server initialize, thread list/read/start and queue/background APIs were exercised on disposable threads.",
            "turn/interrupt was observed to complete an App Server turn as interrupted.",
            "Desktop same-thread identity/UI visibility, busy-thread safety, policy restoration and automatic acknowledgement are not proven.",
            "One earlier handoff survival run passed, while repeat/race runs were UNKNOWN; the survival gate is therefore PARTIAL.",
            "Automatic PreToolUse rewrite/interception remains FAIL and disabled.",
        ],
    }


def _evidence_for(
    name: str,
    status: str,
    app: Dict[str, Any],
    concurrency: Dict[str, Any],
    interrupt: Dict[str, Any],
    security: Dict[str, Any],
    stale: Dict[str, Any],
) -> List[str]:
    sources = {
        "app_server_control": "v0.2 App Server runtime/schema probe",
        "thread_identifier_shared": "thread/start + thread/read same identifier",
        "turn_interrupt": "v0.1 and App Server turn/interrupt probe",
        "busy_thread_delivery": "v0.2 active-turn candidate mechanism probe",
        "idle_check_to_external_turn_race": "v0.2 idle-check TOCTOU probe",
        "delivery_deduplication_and_recovery": "v0.2 J1-J5 durable delivery race probe",
        "project_change_stale": "v0.2 disposable Git mutation probe",
        "actual_command_integrity": "v0.2 JobSpec tamper probe",
        "sandbox_preserved": "v0.2 disposable workspace differential probe",
        "auto_pretool_interception": "v0.2 policy rule; production interception is disabled",
    }
    return [sources.get(name, "v0.1/v0.2 capability aggregation")]


def write_outputs(payload: Dict[str, Any], base: Optional[Path] = None) -> None:
    base = base or OUT
    base.mkdir(parents=True, exist_ok=True)
    path = base / "capabilities.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    flags = payload["feature_flags"]
    lines = [
        "# v0.2 control-plane capability report",
        "",
        f"Generated: `{payload['generated_at']}`",
        f"Codex: `{payload['codex_version']}`",
        f"Control plane: **{payload['control_plane']}**",
        "",
        "| Feature | Status |",
        "|---|---|",
    ]
    lines.extend(f"| `{name}` | **{status}** |" for name, status in flags.items())
    lines.extend(["", "## Required integration flags", "", "| Flag | Status |", "|---|---|"])
    lines.extend(
        f"| `{name}` | **{status}** |"
        for name, status in payload["integration_flags"].items()
    )
    lines.extend(["", "## Automatic feature state", "", "- Auto resume: **disabled**", "- Auto handoff: **disabled**", "- Auto PreToolUse interception: **disabled**", ""])
    lines.extend(["## Decision reasons", ""])
    lines.extend(f"- {reason}" for reason in payload["decision_reasons"])
    lines.extend(["", "## Evidence sources", ""])
    lines.extend(f"- `{name}`: `{path}`" for name, path in payload["sources"].items())
    lines.append("")
    md = base / "capabilities.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    os.chmod(md, 0o600)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate Codex Snooze v0.2 probe results")
    parser.add_argument("--base", type=Path, default=OUT)
    args = parser.parse_args(argv)
    payload = build_payload(args.base)
    write_outputs(payload, args.base)
    print(json.dumps({"control_plane": payload["control_plane"], "flags": payload["feature_flags"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
