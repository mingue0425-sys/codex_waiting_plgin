#!/usr/bin/env python3
from __future__ import annotations

"""Aggregate v0.5 artifacts with a conservative production gate."""

import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.5"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.app_server_probe import redact, write_trace
from snooze_core.models import utc_now


VALID = {"PASS", "PARTIAL", "FAIL", "UNKNOWN", "NOT_SUPPORTED"}


def load(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"status": "UNKNOWN", "invalid": str(path.name)}
    except (OSError, ValueError):
        return {"status": "UNKNOWN", "missing": str(path.name)}


def status(value: Any, default: str = "UNKNOWN") -> str:
    raw = str(value)
    return raw if raw in VALID else default


def build() -> Dict[str, Any]:
    comparison = load(OUT / "backend-comparison.json")
    matrix = comparison.get("backend_matrix", {})
    native = matrix.get("THREAD_NATIVE_TERMINAL", {}) or {}
    descendant = matrix.get("SANDBOX_DESCENDANT_SUPERVISOR", {}) or {}
    c = matrix.get("CONTROLLER_COMMAND_EXEC", {}) or {}
    native_e2e = load(OUT / "native-terminal-e2e.json")
    inheritance = load(OUT / "process-inheritance.json")
    races = load(OUT / "handoff-races.json")
    benchmark = load(OUT / "benchmark.json")
    schema = load(OUT / "native-terminal-schema.json")
    v04 = load(ROOT / "results" / "v0.4" / "capabilities.json")
    v04_flags = v04.get("integration_flags", {})
    flags = {
        "THREAD_NATIVE_TERMINAL": status(native.get("status")),
        "DESCENDANT_SUPERVISOR": status(descendant.get("status")),
        "CONTROLLER_COMMAND_EXEC": "FAIL",
        "NATIVE_SANDBOX_PARITY": status(native.get("sandbox_parity")),
        "NATIVE_APPROVAL_PARITY": status(native.get("approval_parity")),
        "DESCENDANT_SANDBOX_INHERITANCE": status(descendant.get("sandbox_parity")),
        "DESCENDANT_APPROVAL_INTEGRITY": status(descendant.get("approval_parity")),
        "COMMAND_INTEGRITY": status(native.get("command_integrity")),
        "10S_HANDOFF": status(native.get("handoff_10s")),
        "JOB_SURVIVAL": status(native.get("job_survival")),
        "MODEL_IDLE_DURING_WAIT": status(native.get("model_idle")),
        "AUTO_CONTINUATION": status(native.get("auto_continuation")),
        "COMPLETION_DETECTION": status(native_e2e.get("completion_detection")),
        "OWNERSHIP_RACES": status(races.get("status")),
        "APP_SERVER_CRASH_RECOVERY": status(v04_flags.get("APP_SERVER_CRASH_RECOVERY")),
        "CONTROLLER_CRASH_RECOVERY": status(v04_flags.get("CONTROLLER_CRASH_RECOVERY")),
        "DESKTOP_ATTACH": "FAIL",
        "AUTO_PRETOOL_INTERCEPTION": "FAIL",
        "SCHEMA_INVENTORY": status(schema.get("status")),
        "BENCHMARK": status(benchmark.get("status")),
    }
    all_native = all(flags[name] == "PASS" for name in (
        "THREAD_NATIVE_TERMINAL",
        "NATIVE_SANDBOX_PARITY",
        "NATIVE_APPROVAL_PARITY",
        "COMMAND_INTEGRITY",
        "10S_HANDOFF",
        "JOB_SURVIVAL",
        "MODEL_IDLE_DURING_WAIT",
        "AUTO_CONTINUATION",
    ))
    all_descendant = all(flags[name] == "PASS" for name in (
        "DESCENDANT_SUPERVISOR",
        "DESCENDANT_SANDBOX_INHERITANCE",
        "DESCENDANT_APPROVAL_INTEGRITY",
        "COMMAND_INTEGRITY",
        "10S_HANDOFF",
        "JOB_SURVIVAL",
        "MODEL_IDLE_DURING_WAIT",
        "AUTO_CONTINUATION",
    ))
    safe = "PASS" if all_native or all_descendant else "NOT_SUPPORTED"
    return redact({
        "schema_version": 1,
        "generated_at": utc_now(),
        "control_plane": "SNOOZE_APP_SERVER_EXPERIMENTAL",
        "integration_flags": flags,
        "backend_matrix": matrix,
        "safe_automatic_handoff": safe,
        "selected_production_path": "SECURITY_PRESERVING_NATIVE_HANDOFF" if safe == "PASS" else "CLI_RESUME_FALLBACK",
        "fallback_path": "CLI_RESUME_FALLBACK",
        "production_automation": "ENABLED" if safe == "PASS" else "DISABLED_SECURITY_GATE",
        "automatic_features_enabled": [],
        "evidence_files": [
            "native-terminal-schema.json",
            "native-terminal-e2e.json",
            "sandbox-parity.json",
            "approval-parity.json",
            "process-inheritance.json",
            "backend-comparison.json",
            "handoff-races.json",
            "benchmark.json",
        ],
        "immutable_reference": {
            "CONTROLLER_COMMAND_EXEC_SECURITY": "FAIL",
            "v04_security_artifact": "../v0.4/security-parity.json",
            "reason": "controller-level command/exec allowed a sibling write under the v0.4 fixture",
        },
        "exactly_once": "NOT_CLAIMED",
        "notes": [
            "Normal thread command events prove a tool item only; they do not by themselves prove native background ownership.",
            "The normal-path sandbox and approval artifacts remain separate capability decisions.",
            "No command is started by the v0.5 controller implementation for candidate backend A or B.",
            "Desktop attach and automatic PreToolUse interception remain FAIL.",
        ],
    })


def markdown(value: Dict[str, Any]) -> str:
    flags = value["integration_flags"]
    lines = [
        "# Codex Snooze v0.5 capabilities",
        "",
        "```text",
        f"THREAD NATIVE TERMINAL = {flags['THREAD_NATIVE_TERMINAL']}",
        f"DESCENDANT SUPERVISOR = {flags['DESCENDANT_SUPERVISOR']}",
        "CONTROLLER COMMAND/EXEC = FAIL",
        "",
        f"NATIVE SANDBOX PARITY = {flags['NATIVE_SANDBOX_PARITY']}",
        f"NATIVE APPROVAL PARITY = {flags['NATIVE_APPROVAL_PARITY']}",
        f"COMMAND INTEGRITY = {flags['COMMAND_INTEGRITY']}",
        "",
        f"10S HANDOFF = {flags['10S_HANDOFF']}",
        f"MODEL IDLE DURING WAIT = {flags['MODEL_IDLE_DURING_WAIT']}",
        f"AUTO CONTINUATION = {flags['AUTO_CONTINUATION']}",
        "",
        f"SAFE AUTOMATIC HANDOFF = {value['safe_automatic_handoff']}",
        f"PRODUCTION PATH = {value['selected_production_path']}",
        "FALLBACK PATH = CLI_RESUME_FALLBACK",
        "",
        "DESKTOP ATTACH = FAIL",
        "AUTO PRETOOL INTERCEPTION = FAIL",
        "```",
        "",
        "| Capability | Status |",
        "|---|---|",
    ]
    lines.extend(f"| `{name}` | **{item}** |" for name, item in flags.items())
    lines.extend(["", f"Production automation: **{value['production_automation']}**", ""])
    lines.extend(f"- {note}" for note in value["notes"])
    return "\n".join(lines) + "\n"


def main() -> int:
    value = build()
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "capabilities.json", value)
    (OUT / "capabilities.md").write_text(markdown(value), encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
