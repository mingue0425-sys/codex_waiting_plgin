#!/usr/bin/env python3
from __future__ import annotations

"""Trace installed App Server protocol surfaces and compare them with source."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.7"
SCHEMA = OUT / "runtime-schema"
SOURCE_URLS = {
    "unified_exec": "https://github.com/openai/codex/blob/main/codex-rs/core/src/unified_exec/mod.rs",
    "process_manager": "https://github.com/openai/codex/blob/main/codex-rs/core/src/unified_exec/process_manager.rs",
    "exec_handler": "https://github.com/openai/codex/blob/main/codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs",
    "app_server_readme": "https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md",
}
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.app_server_probe import redact, utc_now, write_trace
from snooze_controller.model_policy import LUNA_MODEL, MODEL_POLICY, REASONING_EFFORT


def run(argv: list[str]) -> Dict[str, Any]:
    try:
        value = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
        return {"argv": argv, "returncode": value.returncode, "stdout": value.stdout.strip(), "stderr": value.stderr.strip()}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"argv": argv, "returncode": 124, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}


def files() -> list[Path]:
    return sorted(SCHEMA.rglob("*.json")) if SCHEMA.exists() else []


def text_has(term: str) -> bool:
    for path in files():
        try:
            if term in path.read_text(encoding="utf-8"):
                return True
        except OSError:
            continue
    return False


def schema_fields() -> Dict[str, Any]:
    terms = [
        "thread/backgroundTerminals/list",
        "thread/backgroundTerminals/terminate",
        "thread/backgroundTerminals/clean",
        "turn/interrupt",
        "item/commandExecution/outputDelta",
        "item/completed",
        "yieldTimeMs",
        "yield_time_ms",
        "processId",
        "osPid",
        "exitCode",
    ]
    return {term: text_has(term) for term in terms}


def model_schema_fields() -> Dict[str, Any]:
    """Read the installed v2 request schemas before constructing requests."""

    result: Dict[str, Any] = {}
    for surface, filename in (("thread/start", "ThreadStartParams.json"), ("turn/start", "TurnStartParams.json")):
        records: list[Dict[str, Any]] = []
        for channel in ("stable", "experimental"):
            path = SCHEMA / channel / "v2" / filename
            try:
                parsed = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            properties = parsed.get("properties", {}) if isinstance(parsed, dict) else {}
            records.append({
                "channel": channel,
                "file": str(path.relative_to(SCHEMA)),
                "fields": sorted(properties),
                "required": parsed.get("required", []),
                "title": parsed.get("title"),
            })
        result[surface] = records
    return result


def json_file_summary() -> Dict[str, Any]:
    values = files()
    definition_count = 0
    sha256: Dict[str, str] = {}
    for path in values:
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(parsed, dict):
            definition_count += len(parsed.get("definitions", {}))
            sha256[str(path.relative_to(SCHEMA))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"json_file_count": len(values), "definition_count_sum": definition_count, "sha256": sha256}


def build() -> tuple[Dict[str, Any], Dict[str, Any]]:
    version = run(["codex", "--version"])
    help_output = run(["codex", "app-server", "--help"])
    installed = {
        "binary_command": "codex",
        "version_command": version,
        "app_server_help": help_output,
        "schema": json_file_summary(),
        "protocol_terms": schema_fields(),
        "model_policy_schema": model_schema_fields(),
        "model_policy": MODEL_POLICY,
        "requested_model": LUNA_MODEL,
        "reasoning_effort": REASONING_EFFORT,
        "authoritative_for_capability": True,
        "source_revision_match": "NOT_ESTABLISHED",
    }
    source_trace = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "installed_runtime": installed,
        "upstream_reference": {
            "repository": "https://github.com/openai/codex",
            "branch": "main",
            "revision": "not pinned by installed codex-cli 0.153.4",
            "source_revision_match": "NOT_ESTABLISHED",
            "sources": [
                {
                    "name": "UnifiedExecProcessManager / ProcessStore",
                    "url": SOURCE_URLS["unified_exec"],
                    "finding": "The current source keeps ProcessEntry values in a HashMap keyed by an i32 process_id and carries call_id/session/cwd separately from the child process. This is a logical App Server manager identity, not evidence that the value is an OS PID.",
                },
                {
                    "name": "yield deadline calculation",
                    "url": SOURCE_URLS["process_manager"],
                    "finding": "The current source clamps empty write_stdin polling to a minimum and configurable background timeout, while non-empty writes use the interactive maximum. This applies to unified exec polling and does not prove normal-thread native yield.",
                },
                {
                    "name": "normal command handler",
                    "url": SOURCE_URLS["exec_handler"],
                    "finding": "The handler constructs ExecCommandRequest with process_id, yield_time_ms, cwd, sandbox and approval context, then delegates to UnifiedExecProcessManager. The installed protocol surface must still be checked independently.",
                },
                {
                    "name": "background terminal API",
                    "url": SOURCE_URLS["app_server_readme"],
                    "finding": "The current App Server documentation describes background terminal list/terminate/clean as experimental and shows command, cwd, itemId, logical processId and nullable host osPid. It explicitly separates App Server process identity from nullable host metadata.",
                },
            ],
        },
        "data_flow": [
            "normal thread turn -> commandExecution item lifecycle",
            "command item -> logical process/session metadata when exposed",
            "runtime output -> nonce and workspace marker",
            "self-reported pid/ppid -> bounded ps observer",
            "completion item -> exitCode when the same item can be correlated",
        ],
        "model_policy": {
            "policy": MODEL_POLICY,
            "requested_model": LUNA_MODEL,
            "reasoning_effort": REASONING_EFFORT,
            "schema_fields": installed["model_policy_schema"],
            "enforcement": "Every real Codex App Server/exec command is explicitly pinned; runtime, thread and turn telemetry are attested and missing values fail closed.",
        },
        "answers": {
            "processId_generation": "Installed schema alone does not disclose the allocator; current source identifies a manager-local logical process key.",
            "processId_os_pid": "Not assumed. App Server logical processId and host osPid are separate fields in the documented background terminal surface.",
            "processId_attachment": "Observed where the installed schema exposes processId; runtime proof requires item/output/marker correlation.",
            "background_registry_registration": "Protocol and current README expose an experimental list, but installed runtime observation is required to prove registration for a normal-thread command.",
            "yield_expiry_transition": "Current source has unified-exec polling deadlines; installed normal-thread event trace determines whether the model tool actually yields.",
            "turn_interrupt_survival": "Source and protocol surfaces do not replace the live fixture test; runtime proof is recorded separately.",
            "completion_event": "Installed schema exposes item completion and exitCode fields for commandExecution-shaped items; correlation to the same nonce/process remains a runtime gate.",
        },
        "installed_vs_upstream": [
            {"capability": "yield_time", "installed_runtime": "schema term present only where generated protocol exposes it; normal tool invocation not proven", "upstream_current": "present in ExecCommandRequest and unified-exec manager", "production_claim": "NO"},
            {"capability": "process/session handle", "installed_runtime": "processId/osPid surfaces present in schema; semantics need runtime correlation", "upstream_current": "logical manager process_id plus nullable host osPid in background API", "production_claim": "NO"},
            {"capability": "background terminals", "installed_runtime": "experimental method strings present", "upstream_current": "experimental list/terminate/clean documented", "production_claim": "NO"},
            {"capability": "interrupt survival", "installed_runtime": "no schema-only proof", "upstream_current": "requires lifecycle implementation and live test", "production_claim": "NO"},
            {"capability": "completion event", "installed_runtime": "item completion/exitCode fields present", "upstream_current": "manager returns output/status after process completion", "production_claim": "NO"},
        ],
        "status": "PASS" if installed["version_command"]["returncode"] == 0 else "UNKNOWN",
        "production_selection": "CLI_RESUME_FALLBACK",
        "blocker": "BLOCKED_BY_INSTALLED_CODEX_VERSION_OR_UNPROVEN_NORMAL_THREAD_PATH" if installed["source_revision_match"] != "MATCHED" else None,
    }
    return redact(installed), redact(source_trace)


def write_doc(value: Dict[str, Any]) -> None:
    upstream = value["upstream_reference"]
    lines = [
        "# v0.7 Runtime Source Trace",
        "",
        "Installed runtime evidence is authoritative for capability selection. The generated schema was captured from `codex-cli 0.153.4`; current upstream source is a comparison reference and is not treated as an installed feature.",
        "",
        "## Installed runtime",
        "",
        f"- Version command: `{value['installed_runtime']['version_command']['stdout']}`.",
        f"- Generated schema files: `{value['installed_runtime']['schema']['json_file_count']}`.",
        f"- Protocol terms: `{value['installed_runtime']['protocol_terms']}`.",
        f"- Model policy: `{value['installed_runtime']['model_policy']}`, requested `{value['installed_runtime']['requested_model']}`, effort `{value['installed_runtime']['reasoning_effort']}`.",
        f"- Installed request fields: `{value['installed_runtime']['model_policy_schema']}`.",
        f"- Installed source revision match: `{value['installed_runtime']['source_revision_match']}`.",
        "",
        "## Source trace",
        "",
    ]
    for source in upstream["sources"]:
        lines.extend([f"- [{source['name']}]({source['url']}): {source['finding']}", ""])
    lines.extend([
        "## Luna-only model policy",
        "",
        f"The installed schemas expose `model` on both `thread/start` and `turn/start`, and expose `effort` on `turn/start`. The probe sends `{LUNA_MODEL}` and `{REASONING_EFFORT}` explicitly. Runtime, thread and turn model telemetry is still required; configuration alone is not attestation.",
        "",
        "The key identity result is that `processId` must be modeled as a logical or opaque App Server identity unless runtime evidence proves otherwise. A nullable `osPid` is host metadata and cannot be substituted for the logical handle.",
        "",
        "## Installed versus upstream",
        "",
        "| Capability | Installed runtime | Current upstream reference | Production claim |",
        "|---|---|---|---|",
    ])
    for row in value["installed_vs_upstream"]:
        lines.append(f"| {row['capability']} | {row['installed_runtime']} | {row['upstream_current']} | {row['production_claim']} |")
    lines.extend([
        "",
        "The schema and source comparison describe available surfaces. They do not establish native yield, turn survival, completion correlation, sandbox parity, or continuation. Those claims remain gated by the live normal-thread evidence artifacts.",
        "",
        f"Production selection: `{value['production_selection']}`.",
    ])
    (ROOT / "V0.7_RUNTIME_SOURCE_TRACE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    installed, source_trace = build()
    write_trace(OUT / "runtime-version.json", installed)
    write_trace(OUT / "runtime-source-trace.json", source_trace)
    write_doc(source_trace)
    print(json.dumps(source_trace, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
