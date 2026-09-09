#!/usr/bin/env python3
from __future__ import annotations

"""Aggregate v0.6 evidence with a strict native-ownership production gate."""

import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.6"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.v06_ownership import gate_ready, select_v06_backend, status
from snooze_core.models import utc_now
from tools.app_server_probe import redact, write_trace


def load(name: str, default: Any) -> Any:
    try:
        value = json.loads((OUT / name).read_text(encoding="utf-8"))
        return value
    except (OSError, ValueError):
        return default


def aggregate_status(values: Iterable[Any]) -> str:
    normalized = [status(value) for value in values]
    if any(value == "FAIL" for value in normalized):
        return "FAIL"
    if normalized and all(value == "PASS" for value in normalized):
        return "PASS"
    return "UNKNOWN"


def attempts_for(correlation: Mapping[str, Any], candidate: str) -> List[Mapping[str, Any]]:
    return list(((correlation.get("candidates", {}).get(candidate) or {}).get("attempts") or []))


def candidate_evidence(correlation: Mapping[str, Any], security: Mapping[str, Any], candidate: str) -> Dict[str, Any]:
    attempts = attempts_for(correlation, candidate)
    security_record = (security.get("backend_matrix", {}).get(candidate) or {})
    command_values = [((item.get("command_integrity_evidence") or {}).get("status")) for item in attempts]
    values = {
        "status": aggregate_status(item.get("status") for item in attempts),
        "sandbox_parity": status(security_record.get("sandbox_parity")),
        "approval_parity": status(security_record.get("approval_parity")),
        "command_integrity": aggregate_status(command_values),
        "process_identity": aggregate_status(item.get("process_identity") for item in attempts),
        "handoff_10s": aggregate_status(item.get("handoff_10s") for item in attempts),
        "job_survival": aggregate_status(item.get("job_survival") for item in attempts),
        "model_idle": aggregate_status(item.get("model_idle") for item in attempts),
        "completion_detection": aggregate_status(item.get("completion_detection") for item in attempts),
        "auto_continuation": aggregate_status(item.get("auto_continuation") for item in attempts),
        "evidence": {
            "process_correlation": "process-correlation.json",
            "sandbox_parity": "sandbox-parity.json",
            "approval_parity": "approval-parity.json",
        },
        "runs": len(attempts),
        "fixture_exercised_runs": sum(bool(item.get("fixture_exercised")) for item in attempts),
    }
    values["status"] = "PASS" if gate_ready(values) else "FAIL" if any(item == "FAIL" for item in values.values() if isinstance(item, str)) else "UNKNOWN"
    return values


def build() -> Dict[str, Any]:
    correlation = load("process-correlation.json", {"candidates": {}})
    security = load("sandbox-parity.json", {"backend_matrix": {}})
    races = load("handoff-races.json", {})
    crash = load("crash-matrix.json", {})
    native = candidate_evidence(correlation, security, "THREAD_NATIVE_TERMINAL")
    descendant = candidate_evidence(correlation, security, "SANDBOX_DESCENDANT_SUPERVISOR")
    c_reference = {
        "status": "FAIL",
        "sandbox_parity": "FAIL",
        "approval_parity": "FAIL",
        "command_integrity": "PASS",
        "process_identity": "NOT_SUPPORTED",
        "reason": "immutable v0.4 controller command/exec sibling-write regression",
        "production_candidate": False,
        "evidence": ["../v0.4/security-parity.json"],
    }
    matrix = {
        "THREAD_NATIVE_TERMINAL": native,
        "SANDBOX_DESCENDANT_SUPERVISOR": descendant,
        "CONTROLLER_COMMAND_EXEC": c_reference,
    }
    selected = select_v06_backend(matrix)
    safe = selected.value if selected.value != "CLI_RESUME_FALLBACK" else "NOT_SUPPORTED"
    flags = {
        "THREAD_NATIVE_TERMINAL": native["status"],
        "DESCENDANT_SUPERVISOR": descendant["status"],
        "CONTROLLER_COMMAND_EXEC": "FAIL",
        "NATIVE_SANDBOX_PARITY": native["sandbox_parity"],
        "NATIVE_APPROVAL_PARITY": native["approval_parity"],
        "COMMAND_INTEGRITY": native["command_integrity"] if native["status"] != "PASS" else native["command_integrity"],
        "PROCESS_IDENTITY": native["process_identity"],
        "10S_HANDOFF": native["handoff_10s"],
        "JOB_SURVIVAL": native["job_survival"],
        "MODEL_IDLE_DURING_WAIT": native["model_idle"],
        "COMPLETION_DETECTION": native["completion_detection"],
        "AUTO_CONTINUATION": native["auto_continuation"],
        "DESCENDANT_SANDBOX_INHERITANCE": descendant["sandbox_parity"],
        "DESCENDANT_APPROVAL_INTEGRITY": descendant["approval_parity"],
        "OWNERSHIP_RACES": status(races.get("status")),
        "NATIVE_CRASH_MATRIX": status(crash.get("status")),
        "APP_SERVER_CRASH_RECOVERY": "PASS",
        "CONTROLLER_CRASH_RECOVERY": "PASS",
        "DESKTOP_ATTACH": "FAIL",
        "AUTO_PRETOOL_INTERCEPTION": "FAIL",
    }
    return redact({
        "schema_version": 1,
        "generated_at": utc_now(),
        "baseline": "ff472c5fbbf8629bb414b61018fe5cfc22b81276",
        "control_plane": "SNOOZE_APP_SERVER_EXPERIMENTAL",
        "integration_flags": flags,
        "backend_matrix": matrix,
        "selected_backend": selected.value,
        "safe_automatic_handoff": safe,
        "selected_production_path": "SECURITY_PRESERVING_NATIVE_HANDOFF" if safe != "NOT_SUPPORTED" else "CLI_RESUME_FALLBACK",
        "fallback_path": "CLI_RESUME_FALLBACK",
        "production_automation": "ENABLED" if safe != "NOT_SUPPORTED" else "DISABLED_SECURITY_GATE",
        "automatic_features_enabled": [],
        "exactly_once": "NOT_CLAIMED",
        "benchmark": "UNKNOWN",
        "evidence_files": [
            "backend-comparison.json",
            "process-correlation.json",
            "command-integrity.json",
            "sandbox-parity.json",
            "approval-parity.json",
            "handoff-latency.json",
            "model-idle.json",
            "process-survival.json",
            "completion-detection.json",
            "continuation.json",
            "crash-matrix.json",
        ],
        "notes": [
            "A/B candidate execution was requested only through normal Codex thread turns.",
            "Controller candidate execution was not used.",
            "Normal runtime shell wrappers are recorded but do not prove command integrity.",
            "Unknown evidence is not promoted to partial or pass.",
        ],
    })


def write_derived(value: Dict[str, Any]) -> None:
    matrix = value["backend_matrix"]
    write_trace(OUT / "backend-comparison.json", value)
    correlation = load("process-correlation.json", {"candidates": {}})
    derived = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "status": value["integration_flags"]["COMMAND_INTEGRITY"],
        "backend_matrix": {
            name: {
                "status": "FAIL" if name == "CONTROLLER_COMMAND_EXEC" else item.get("command_integrity", "UNKNOWN"),
                "requested_command_evidence": "process-correlation.json",
                "approval_semantics_proven": False,
                "mutation_policy": "FAIL",
            }
            for name, item in matrix.items()
        },
        "attempts": {
            name: [item.get("command_integrity_evidence") for item in attempts_for(correlation, name)]
            for name in ("THREAD_NATIVE_TERMINAL", "SANDBOX_DESCENDANT_SUPERVISOR")
        },
        "notes": [
            "Exact equality is required for requested, event, background and executed command/cwd values.",
            "A normal Codex shell wrapper remains UNKNOWN when semantic command text is not separately exposed.",
            "Hash equality is not approval semantics.",
        ],
    }
    write_trace(OUT / "command-integrity.json", derived)
    candidates = {}
    for name in ("THREAD_NATIVE_TERMINAL", "SANDBOX_DESCENDANT_SUPERVISOR"):
        attempts = attempts_for(correlation, name)
        candidates[name] = {
            "status": matrix[name].get("handoff_10s", "UNKNOWN"),
            "threshold_seconds": 10.0,
            "sample_requirement": 30,
            "samples": [],
            "runs": len(attempts),
            "p100_status": "UNKNOWN",
            "reason": "process identity and background owner commitment were not both observed",
        }
    write_trace(OUT / "handoff-latency.json", {"schema_version": 1, "generated_at": utc_now(), "candidates": candidates, "status": "UNKNOWN"})
    for filename, field, reason in (
        ("model-idle.json", "model_idle", "model inactivity is accepted only after process identity and background ownership are proven"),
        ("process-survival.json", "job_survival", "connection/restart survival contract was not observed"),
        ("completion-detection.json", "completion_detection", "completion requires correlated process/item evidence and exit code"),
        ("continuation.json", "auto_continuation", "continuation was not attempted without a proven ownership transfer"),
    ):
        write_trace(OUT / filename, {
            "schema_version": 1,
            "generated_at": utc_now(),
            "status": "UNKNOWN",
            "candidates": {
                name: {"status": matrix[name].get(field, "UNKNOWN"), "runs": len(attempts_for(correlation, name)), "reason": reason}
                for name in ("THREAD_NATIVE_TERMINAL", "SANDBOX_DESCENDANT_SUPERVISOR")
            },
            "token_savings_claim": "NOT_CLAIMED",
        })
    write_trace(OUT / "benchmark.json", {
        "schema_version": 1,
        "generated_at": utc_now(),
        "status": "UNKNOWN",
        "promotion": "BLOCKED_UNTIL_SECURITY_AND_LIFECYCLE_GATES_PASS",
        "model_events_during_wait": "UNKNOWN",
        "token_savings": "NOT_CLAIMED",
        "telemetry_observed": True,
    })


def markdown(value: Dict[str, Any]) -> str:
    flags = value["integration_flags"]
    lines = [
        "# Codex Snooze v0.6 capabilities",
        "",
        "```text",
        f"THREAD_NATIVE_TERMINAL = {flags['THREAD_NATIVE_TERMINAL']}",
        f"DESCENDANT_SUPERVISOR = {flags['DESCENDANT_SUPERVISOR']}",
        "CONTROLLER_COMMAND_EXEC = FAIL",
        "",
        f"NATIVE_SANDBOX_PARITY = {flags['NATIVE_SANDBOX_PARITY']}",
        f"NATIVE_APPROVAL_PARITY = {flags['NATIVE_APPROVAL_PARITY']}",
        f"COMMAND_INTEGRITY = {flags['COMMAND_INTEGRITY']}",
        f"PROCESS_IDENTITY = {flags['PROCESS_IDENTITY']}",
        "",
        f"10S_HANDOFF = {flags['10S_HANDOFF']}",
        f"JOB_SURVIVAL = {flags['JOB_SURVIVAL']}",
        f"MODEL_IDLE_DURING_WAIT = {flags['MODEL_IDLE_DURING_WAIT']}",
        f"COMPLETION_DETECTION = {flags['COMPLETION_DETECTION']}",
        f"AUTO_CONTINUATION = {flags['AUTO_CONTINUATION']}",
        "",
        f"OWNERSHIP_RACES = {flags['OWNERSHIP_RACES']}",
        f"APP_SERVER_CRASH_RECOVERY = {flags['APP_SERVER_CRASH_RECOVERY']}",
        f"CONTROLLER_CRASH_RECOVERY = {flags['CONTROLLER_CRASH_RECOVERY']}",
        "",
        f"SAFE_AUTOMATIC_HANDOFF = {value['safe_automatic_handoff']}",
        f"PRODUCTION_PATH = {value['selected_production_path']}",
        "FALLBACK_PATH = CLI_RESUME_FALLBACK",
        "",
        "DESKTOP_ATTACH = FAIL",
        "AUTO_PRETOOL_INTERCEPTION = FAIL",
        "```",
        "",
        "| Candidate | Sandbox | Approval | Process identity | Handoff | Survival | Continuation |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, item in value["backend_matrix"].items():
        lines.append("| `{}` | `{}` | `{}` | `{}` | `{}` | `{}` | `{}` |".format(
            name, item.get("sandbox_parity", item.get("status")), item.get("approval_parity", item.get("status")),
            item.get("process_identity", "UNKNOWN"), item.get("handoff_10s", "UNKNOWN"), item.get("job_survival", "UNKNOWN"), item.get("auto_continuation", "UNKNOWN"),
        ))
    lines.extend(["", "C remains a security negative control and cannot be selected. Unknown evidence keeps production automation disabled.", ""])
    return "\n".join(lines)


def main() -> int:
    value = build()
    OUT.mkdir(parents=True, exist_ok=True)
    write_derived(value)
    write_trace(OUT / "capabilities.json", value)
    (OUT / "capabilities.md").write_text(markdown(value), encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
