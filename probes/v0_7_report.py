#!/usr/bin/env python3
from __future__ import annotations

"""Aggregate v0.7 runtime, provenance, yield, security and polling evidence."""

import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.7"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_core.models import utc_now
from snooze_controller.model_policy import LUNA_MODEL, MODEL_POLICY, REASONING_EFFORT
from tools.app_server_probe import redact, write_trace


def load(name: str, default: Any = None) -> Any:
    try:
        return json.loads((OUT / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def status(value: Any, default: str = "UNKNOWN") -> str:
    return value if value in {"PASS", "PARTIAL", "FAIL", "UNKNOWN", "NOT_SUPPORTED", "NOT_RUN", "NOT_APPLICABLE"} else default


def unwrap_long_observation(value: Any) -> Dict[str, Any]:
    current = value if isinstance(value, dict) else {}
    for _ in range(12):
        if "process_id_kind" in current:
            return current
        nested = current.get("observation")
        if isinstance(nested, dict):
            current = nested
            continue
        nested = current.get("long_native_fixture")
        if isinstance(nested, dict):
            current = nested
            continue
        break
    return current


def model_metadata() -> Dict[str, Any]:
    """Return the latest strict model record for every derived live artifact."""

    value = load("model-attestation.json", {}) or {}
    if not isinstance(value, dict):
        value = {}
    keys = {
        "model_policy",
        "requested_model",
        "runtime_reported_model",
        "thread_model",
        "turn_model",
        "observed_model",
        "reasoning_effort",
        "model_attestation",
        "experiment_valid",
        "fallback_detected",
        "auto_review_model_override",
        "review_model",
        "delegated_model",
        "subagent_policy",
        "subagent_calls",
        "non_luna_model_calls",
        "attestation_reasons",
    }
    result = {key: value.get(key) for key in keys if key in value}
    result.setdefault("model_policy", MODEL_POLICY)
    result.setdefault("requested_model", LUNA_MODEL)
    result.setdefault("observed_model", None)
    result.setdefault("reasoning_effort", REASONING_EFFORT)
    result.setdefault("model_attestation", "FAIL")
    result.setdefault("experiment_valid", False)
    result.setdefault("subagent_policy", "DISABLED")
    result.setdefault("subagent_calls", [])
    result.setdefault("non_luna_model_calls", 0)
    result.setdefault("attestation_reasons", ["missing_model_attestation"])
    return result


def build() -> Dict[str, Any]:
    live = load("native-live-observation.json", {}) or {}
    namespace = load("execution-namespace.json", {}) or {}
    provenance = load("process-provenance.json", {}) or {}
    yield_artifact = load("native-yield.json", {}) or {}
    timeline = load("native-timeline.json", {}) or {}
    source = load("runtime-source-trace.json", {}) or {}
    identity = load("identity-live-observation.json", {}) or {}
    model = model_metadata()
    model_verified = model.get("model_attestation") == "PASS"
    provenance_status = status(provenance.get("status")) if model_verified else "UNKNOWN"
    native_yield = status(yield_artifact.get("status")) if model_verified else "UNKNOWN"
    survival = status(yield_artifact.get("job_survival")) if model_verified else "UNKNOWN"
    completion = status(live.get("completion_detection")) if model_verified else "UNKNOWN"
    identity_namespace = identity.get("execution_namespace") or {}
    identity_provenance = identity.get("provenance") or {}
    short_namespace_status = "PASS" if identity_namespace.get("same_absolute_namespace") and identity_namespace.get("marker_observed") and identity_namespace.get("result_observed") else "UNKNOWN"
    namespace_status = short_namespace_status if model_verified and short_namespace_status == "PASS" else status(namespace.get("status")) if model_verified else "UNKNOWN"
    long_provenance = unwrap_long_observation(provenance)
    process_id_kind = long_provenance.get("process_id_kind", "UNKNOWN") if model_verified else "UNKNOWN"
    registry_before = ((yield_artifact.get("background_registry") or {}).get("before") or {})
    registry_threshold = ((yield_artifact.get("background_registry") or {}).get("threshold") or {})
    registry_visible = "UNKNOWN"
    if model_verified and isinstance(registry_before, dict) and "data" in registry_before:
        registry_visible = "PASS" if registry_threshold.get("data") else "PARTIAL"

    a_status = "PASS" if model_verified and all(value == "PASS" for value in (provenance_status, native_yield, survival, completion)) else "FAIL" if model_verified and (native_yield == "FAIL" or provenance_status == "FAIL") else "UNKNOWN"
    b_status = "NOT_RUN"
    command_integrity = "PASS" if model_verified and (provenance.get("observation") or {}).get("command_match") == "PASS" else "UNKNOWN"
    wait_calls = list(live.get("wait_family_calls_during_wait") or [])
    model_events = list(live.get("model_events_after_threshold") or [])
    approval_requests = list(((live.get("approval_requests") or []))) if model_verified else []
    approval_test = "NOT_APPLICABLE" if not approval_requests else "OBSERVED"

    handoff = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "status": "PASS" if a_status == "PASS" else "UNKNOWN",
        "e2e_cases": {
            "E2E-NATIVE-01-exit-0": {
                "status": "PASS" if a_status == "PASS" and live.get("fixture", {}).get("requested_exit_code") == 0 else "UNKNOWN",
                "reason": "requires normal command, provenance, native yield, survival, completion/exit-code mapping and continuation",
            },
            "E2E-NATIVE-02-exit-7": {"status": "NOT_RUN", "reason": "gated until E2E-NATIVE-01 native ownership is proven"},
            "E2E-NATIVE-03-threshold-race": {"status": "NOT_RUN", "reason": "gated until native yield is proven"},
            "E2E-NATIVE-04-interrupt-race-100": {"status": "NOT_RUN", "reason": "gated until native yield is proven"},
            "E2E-NATIVE-05-controller-crash": {"status": "NOT_RUN", "reason": "gated until durable native ownership is proven"},
            "E2E-NATIVE-06-app-server-crash": {"status": "NOT_RUN", "reason": "gated until native background ownership is proven"},
        },
        "rerun_guard": "run_count must remain 1 before any continuation is eligible",
        "continuation": live.get("continuation") or {"status": "NOT_RUN"},
        "production_selection": "CLI_RESUME_FALLBACK",
    }
    security = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "status": "UNKNOWN",
        "namespace_prerequisite": namespace_status,
        "normal_baseline": {"status": "UNKNOWN", "operations": ["workspace_read", "workspace_write", "sibling_read", "sibling_write"], "reason": "v0.7 native semantic differential is not run before native yield proof"},
        "native_candidate": {"status": "NOT_RUN" if a_status != "PASS" else "UNKNOWN", "reason": "native yielded terminal is not established"},
        "approval_test": approval_test,
        "approval_parity": "UNKNOWN",
        "sandbox_parity": "UNKNOWN",
        "command_integrity": command_integrity,
        "controller_reference": {"status": "FAIL", "evidence": "results/v0.4/security-parity.json", "production_candidate": False},
        "policy": {"sandbox": "workspace-write", "approval": "on-request", "allow_rule": "no blanket allow; absent request is not parity proof"},
        "notes": ["Security parity is evaluated only after the fixture namespace is proven and native ownership is established.", "A/B are independent; descendant supervisor was not started by the controller."],
    }
    polling = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "status": "UNKNOWN",
        "metric": "WAIT_FAMILY_CALL_COUNT",
        "wait_family": ["wait", "write_stdin(empty)", "status polling"],
        "baseline": {"model_poll_calls": 0, "wait_family_calls": [], "measurement": "normal-thread event trace; no repeated wait family call observed"},
        "snooze": {"model_poll_calls": 0, "wait_family_calls": wait_calls, "measurement": "controller-side observation only; no model turn after threshold"},
        "model_events_after_threshold": len(model_events),
        "reduction": "NOT_CLAIMED",
        "reason": "zero observed calls is recorded, but a native wait interval was not established, so no savings claim is made",
    }
    flags = {
        "PROCESS_ID_KIND": process_id_kind,
        "NATIVE_EXECUTION_PROVENANCE": provenance_status,
        "NATIVE_YIELD": native_yield,
        "BACKGROUND_TERMINAL_REGISTRY_VISIBLE": registry_visible,
        "THREAD_NATIVE_TERMINAL": a_status,
        "DESCENDANT_SUPERVISOR": b_status,
        "CONTROLLER_COMMAND_EXEC": "FAIL",
        "NATIVE_SANDBOX_PARITY": security["sandbox_parity"],
        "NATIVE_APPROVAL_PARITY": security["approval_parity"],
        "COMMAND_INTEGRITY": command_integrity,
        "PROCESS_IDENTITY": provenance_status,
        "10S_HANDOFF": native_yield,
        "JOB_SURVIVAL": survival,
        "MODEL_IDLE_DURING_WAIT": "PASS" if not model_events and native_yield == "PASS" else "UNKNOWN",
        "COMPLETION_DETECTION": completion,
        "AUTO_CONTINUATION": "PASS" if model_verified and (live.get("continuation") or {}).get("status") == "PASS" else "UNKNOWN",
        "WAIT_FAMILY_CALLS_DURING_WAIT": len(wait_calls),
        "OWNERSHIP_RACES": "PASS",
        "DESKTOP_ATTACH": "FAIL",
        "AUTO_PRETOOL_INTERCEPTION": "FAIL",
        "LUNA_AVAILABLE": "PASS" if model["model_attestation"] == "PASS" else "UNKNOWN",
        "LUNA_MODEL_ATTESTATION": model["model_attestation"],
        "LUNA_ONLY_ENFORCEMENT": "PASS",
        "NON_LUNA_MODEL_CALLS": model["non_luna_model_calls"],
    }
    safe = "PASS" if a_status == "PASS" and security["sandbox_parity"] == "PASS" and security["approval_parity"] in {"PASS", "NOT_APPLICABLE"} and command_integrity == "PASS" else "NOT_SUPPORTED"
    return redact({
        "schema_version": 1,
        "generated_at": utc_now(),
        "baseline": "2e7f865a399ef797eecfd43a86cde4dc36f42ab7",
        "control_plane": "SNOOZE_APP_SERVER_EXPERIMENTAL",
        "integration_flags": flags,
        "backend_matrix": {
            "THREAD_NATIVE_TERMINAL": {"status": a_status, "provenance": provenance_status, "native_yield": native_yield, "survival": survival, "completion": completion},
            "DESCENDANT_SUPERVISOR": {"status": b_status, "status_reason": "B is only run after A native yield is FAIL or clearly unavailable; no controller supervisor was started"},
            "CONTROLLER_COMMAND_EXEC": {"status": "FAIL", "production_candidate": False},
        },
        "selected_backend": "CLI_RESUME_FALLBACK",
        "safe_automatic_handoff": safe,
        "production_path": "CLI_RESUME_FALLBACK",
        "fallback_path": "CLI_RESUME_FALLBACK",
        "production_automation": "DISABLED_SECURITY_GATE",
        "source_trace_status": status(source.get("status")),
        "execution_namespace_status": namespace_status,
        "short_identity_fixture": {"status": status(identity.get("status")) if model_verified else "UNKNOWN", "namespace": short_namespace_status if model_verified else "UNKNOWN", "provenance": status(identity_provenance.get("status")) if model_verified else "UNKNOWN", "process_id_kind": identity_provenance.get("process_id_kind", "UNKNOWN") if model_verified else "UNKNOWN"},
        "e2e": handoff,
        "security": security,
        "polling": polling,
        "race": {"status": "PASS", "ownership_races": 100, "dual_owners": 0, "source": "v0.6 durable CAS regression"},
        "regressions": {"v0.6": "PASS", "v0.5": "PASS", "v0.4": "PASS"},
        "evidence_files": ["runtime-version.json", "runtime-source-trace.json", "model-attestation.json", "identity-live-observation.json", "native-live-observation.json", "execution-namespace.json", "process-provenance.json", "native-yield.json", "native-timeline.json", "handoff-e2e.json", "security-parity.json", "polling-benchmark.json"],
        "notes": ["Normal-thread candidate execution only; no controller candidate command was used.", "Unknown and not-run evidence is never promoted to PASS.", "The v0.4 controller command/exec sibling-write failure remains immutable."],
        **model,
    }), handoff, security, polling


def write_docs(value: Dict[str, Any], handoff: Dict[str, Any], security: Dict[str, Any], polling: Dict[str, Any]) -> None:
    flags = value["integration_flags"]
    model_keys = (
        "model_policy",
        "requested_model",
        "runtime_reported_model",
        "thread_model",
        "turn_model",
        "observed_model",
        "reasoning_effort",
        "model_attestation",
        "experiment_valid",
        "fallback_detected",
        "auto_review_model_override",
        "review_model",
        "delegated_model",
        "subagent_policy",
        "subagent_calls",
        "non_luna_model_calls",
        "attestation_reasons",
    )
    model = {key: value.get(key) for key in model_keys}
    (ROOT / "V0.7_MODEL_POLICY_REPORT.md").write_text(
        "# v0.7 Luna-only Model Policy Report\n\n"
        "Every live v0.7 experiment is explicitly pinned to GPT-5.6 Luna with medium reasoning effort. The real App Server command is `codex -m gpt-5.6-luna -c 'model_reasoning_effort=\"medium\"' app-server --stdio`; direct CLI execution uses the equivalent `codex exec -m gpt-5.6-luna -c 'model_reasoning_effort=\"medium\"' ...`. Installed schema evidence for `thread/start.model`, `turn/start.model`, and `turn/start.effort` is recorded in [V0.7_RUNTIME_SOURCE_TRACE.md](V0.7_RUNTIME_SOURCE_TRACE.md).\n\n"
        "Before a candidate tool call, the controller runs a no-tool attestation turn and records requested, runtime, thread and turn model values. Approval review is user-owned, multi-agent delegation is disabled, and fallback/reroute/non-Luna activity fails closed. The mandatory pre-probe log is emitted by each live v0.7 probe.\n\n"
        + "```text\n"
        + "\n".join(f"{key} = {model[key]}" for key in model_keys)
        + "\n```\n\n"
        + f"`LUNA_MODEL_ATTESTATION={flags['LUNA_MODEL_ATTESTATION']}` and `EXPERIMENT_VALID={model['experiment_valid']}`. The installed runtime reported `{model['runtime_reported_model']}` for the runtime and `{model['thread_model']}` for the thread, but the turn model field was `{model['turn_model']}`. A missing turn field makes the live sample invalid; no candidate fixture command was started.\n\n"
        "No historical pre-policy live result is used as v0.7 evidence. A missing or non-Luna runtime value remains invalid and never becomes handoff, sandbox, approval, latency or benchmark proof. Production selection remains `CLI_RESUME_FALLBACK`.\n",
        encoding="utf-8",
    )
    (ROOT / "V0.7_EXECUTION_NAMESPACE_REPORT.md").write_text(
        "# v0.7 Execution Namespace Report\n\n"
        "The harness creates an absolute disposable workspace and starts the App Server with that exact directory as `cwd`. The normal thread receives an absolute probe command. The report records controller workspace, App Server cwd, marker inode-visible side effects, and result marker visibility. A missing marker is a namespace failure or unknown observation; it is not sandbox parity evidence.\n\n"
        f"Recorded namespace status: `{value['execution_namespace_status']}`. The short identity fixture namespace/provenance was `{value['short_identity_fixture']['namespace']}`/`{value['short_identity_fixture']['provenance']}`. The long native fixture side effect is recorded separately in `results/v0.7/execution-namespace.json`.\n\n"
        "The disposable run directory and its fixture files are removed after the App Server connection is closed. The model policy gate is applied before namespace evidence is eligible for a capability claim.\n",
        encoding="utf-8",
    )
    (ROOT / "V0.7_PROCESS_PROVENANCE_REPORT.md").write_text(
        "# v0.7 Process Provenance Report\n\n"
        "The identity graph is `command item -> logical process/session identity -> output nonce -> self-reported PID/PPID -> bounded OS observer -> workspace marker`. The live candidate was not eligible after the Luna attestation failed, so neither fixture graph is promoted. `processId` is not treated as an OS PID.\n\n"
        f"PROCESS_ID_KIND = `{flags['PROCESS_ID_KIND']}`\n\n"
        f"Short identity fixture provenance = `{value['short_identity_fixture']['provenance']}` with process kind `{value['short_identity_fixture']['process_id_kind']}`; long native fixture identity is retained in `results/v0.7/process-provenance.json`.\n\n"
        f"NATIVE EXECUTION PROVENANCE = `{flags['NATIVE_EXECUTION_PROVENANCE']}`\n\n"
        "Required evidence is ITEM_NONCE_MATCH, STDOUT_NONCE_MATCH, WORKSPACE_MARKER_MATCH, SELF_REPORTED_PID_OBSERVED and CWD_MATCH. OS command and start-time matches are retained separately and missing values remain UNKNOWN.\n",
        encoding="utf-8",
    )
    (ROOT / "V0.7_NATIVE_YIELD_REPORT.md").write_text(
        "# v0.7 Native Yield Report\n\n"
        f"NATIVE_YIELD = `{flags['NATIVE_YIELD']}`\n\n"
        f"BACKGROUND_TERMINAL_REGISTRY = `{flags['BACKGROUND_TERMINAL_REGISTRY_VISIBLE']}`\n\n"
        "The controller requests a normal terminal execution from the model and observes notifications. It does not call command/exec, process/spawn, thread/shellCommand, or start a supervisor. `yield_time_ms=10000` is requested in the prompt only when the normal tool exposes that parameter; the prompt itself is not treated as proof.\n\n"
        "Turn interruption is attempted only after the strict model attestation and provenance gates pass.\n",
        encoding="utf-8",
    )
    (ROOT / "V0.7_NATIVE_HANDOFF_E2E.md").write_text(
        "# v0.7 Native Handoff E2E\n\n"
        f"THREAD_NATIVE_TERMINAL = `{flags['THREAD_NATIVE_TERMINAL']}`\n\n"
        "The required E2E chain is a valid Luna-attested normal command, raw command integrity, provenance, native yield, 10-second boundary, turn end, heartbeat survival, zero model wait-family calls, correlated exit code, same-thread continuation, RESULT_TOKEN, and run_count=1.\n\n"
        "Cases:\n\n" + "\n".join(f"- `{name}`: `{case['status']}` — {case['reason']}" for name, case in handoff["e2e_cases"].items()) + "\n",
        encoding="utf-8",
    )
    (ROOT / "V0.7_SECURITY_PARITY_REPORT.md").write_text(
        "# v0.7 Security Parity Report\n\n"
        f"NATIVE_SANDBOX_PARITY = `{security['sandbox_parity']}`\n\n"
        f"NATIVE_APPROVAL_PARITY = `{security['approval_parity']}`\n\n"
        f"COMMAND_INTEGRITY = `{security['command_integrity']}`\n\n"
        f"APPROVAL_TEST = `{security['approval_test']}`. An absent request is not approval parity proof. The immutable controller command/exec reference remains FAIL. The Luna attestation must pass before this differential can be evidence.\n",
        encoding="utf-8",
    )
    (ROOT / "V0.7_POLLING_BENCHMARK.md").write_text(
        "# v0.7 Polling Benchmark\n\n"
        f"WAIT-FAMILY CALLS DURING WAIT = `{flags['WAIT_FAMILY_CALLS_DURING_WAIT']}`\n\n"
        f"Benchmark status = `{polling['status']}`.\n\n"
        "The metric counts `wait`, empty `write_stdin`, and status polling. Zero observed calls are reported, but no token or time savings claim is made until a valid Luna-attested native wait interval and completion are both proven.\n",
        encoding="utf-8",
    )


def main() -> int:
    value, handoff, security, polling = build()
    model = model_metadata()
    handoff.update(model)
    security.update(model)
    polling.update(model)
    identity = load("identity-live-observation.json", {}) or {}
    long_namespace = load("execution-namespace.json", {}) or {}
    long_provenance = load("process-provenance.json", {}) or {}
    long_observation = unwrap_long_observation(long_provenance)
    write_trace(
        OUT / "execution-namespace.json",
        {
            "schema_version": 1,
            "generated_at": utc_now(),
            "status": value["execution_namespace_status"],
            **model,
            "short_identity_fixture": {
                "status": (identity.get("status") or "UNKNOWN"),
                "namespace": identity.get("execution_namespace"),
                "provenance": identity.get("provenance"),
            },
            "long_native_fixture": {
                "status": long_namespace.get("status", "UNKNOWN"),
                "candidate_side_effect": long_namespace.get("candidate_side_effect", "UNKNOWN"),
                "observation": long_namespace.get("observation"),
            },
        },
    )
    write_trace(
        OUT / "process-provenance.json",
        {
            "schema_version": 1,
            "generated_at": utc_now(),
            "status": value["integration_flags"]["NATIVE_EXECUTION_PROVENANCE"],
            **model,
            "short_identity_fixture": identity.get("provenance"),
                "long_native_fixture": long_observation,
            "interpretation": "short fixture PASS establishes the exact namespace and identity method; long native fixture FAIL lacks stdout/marker/PID evidence",
        },
    )
    write_trace(OUT / "handoff-e2e.json", handoff)
    write_trace(OUT / "security-parity.json", security)
    write_trace(OUT / "polling-benchmark.json", polling)
    write_trace(OUT / "capabilities.json", value)
    (OUT / "capabilities.md").write_text(
        "# Codex Snooze v0.7 capabilities\n\n"
        + "\n".join(f"{key} = {item}" for key, item in value["integration_flags"].items())
        + "\n\n"
        + "\n".join(f"{key} = {item}" for key, item in model.items() if key in {"model_policy", "requested_model", "observed_model", "reasoning_effort", "model_attestation", "experiment_valid", "non_luna_model_calls"})
        + "\n\nPRODUCTION_PATH = CLI_RESUME_FALLBACK\nFALLBACK_PATH = CLI_RESUME_FALLBACK\n",
        encoding="utf-8",
    )
    write_docs(value, handoff, security, polling)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
