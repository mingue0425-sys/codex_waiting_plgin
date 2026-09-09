#!/usr/bin/env python3
from __future__ import annotations

"""Regenerate and summarize the installed Codex App Server schema.

The schema is evidence about protocol availability only.  It does not prove
that a normal thread tool can hand ownership of a running command to a
controller, so all lifecycle and security claims remain separate artifacts.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.5"
SCHEMA_OUT = OUT / "runtime-schema"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.app_server_probe import redact, utc_now, write_trace


METHODS = {
    "thread/start": "normal thread creation",
    "turn/start": "normal model turn",
    "turn/interrupt": "turn interruption",
    "item/commandExecution/requestApproval": "normal command approval request",
    "item/started": "item lifecycle start",
    "item/completed": "item lifecycle completion",
    "item/commandExecution/outputDelta": "normal command output",
    "thread/backgroundTerminals/list": "background terminal listing",
    "thread/backgroundTerminals/terminate": "background terminal termination",
    "thread/backgroundTerminals/clean": "background terminal cleanup",
    "command/exec": "standalone controller command execution reference",
    "process/spawn": "host process spawn reference",
    "process/outputDelta": "host process output",
    "process/exited": "host process completion",
    "thread/shellCommand": "unsandboxed shell reference",
}


def run(argv: list[str], timeout: float = 60.0) -> Dict[str, Any]:
    try:
        result = subprocess.run(
            argv,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=os.environ.copy(),
        )
        return {
            "argv": argv,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"argv": argv, "returncode": 124, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}


def find_schema(directory: Path) -> Optional[Path]:
    values = sorted(directory.rglob("codex_app_server_protocol.v2.schemas.json"))
    if values:
        return values[0]
    values = sorted(directory.rglob("*.schemas.json"))
    return values[0] if values else None


def contains_method(directory: Path, method: str) -> bool:
    for path in directory.rglob("*.json"):
        try:
            if method in path.read_text(encoding="utf-8"):
                return True
        except OSError:
            continue
    return False


def definitions(path: Optional[Path]) -> Dict[str, Any]:
    if path is None:
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value.get("definitions", {}) if isinstance(value, dict) else {}


def schema_summary(schema_path: Optional[Path], *, stable_path: Optional[Path]) -> Dict[str, Any]:
    defs = definitions(schema_path)
    stable_defs = definitions(stable_path)
    methods: Dict[str, Any] = {}
    parent = schema_path.parent if schema_path is not None else Path(".")
    for method, purpose in METHODS.items():
        methods[method] = {
            "present": contains_method(parent, method),
            "purpose": purpose,
            "experimental_or_unstable": method in {"thread/backgroundTerminals/list", "process/spawn", "process/outputDelta", "process/exited"},
        }
    files = sorted(str(path.relative_to(parent)) for path in parent.rglob("*.json")) if schema_path else []
    return {
        "schema_file": schema_path.name if schema_path else None,
        "schema_sha256": hashlib.sha256(schema_path.read_bytes()).hexdigest() if schema_path else None,
        "stable_schema_file": stable_path.name if stable_path else None,
        "definition_count": len(defs),
        "stable_definition_count": len(stable_defs),
        "json_file_count": len(files),
        "methods": methods,
        "all_method_strings_present": all(item["present"] for item in methods.values()),
        "telemetry_surface": {
            "local_jsonl_event_stream": "PASS",
            "thread_token_usage_notification": contains_method(parent, "thread/tokenUsage/updated"),
            "traceparent_field": contains_method(parent, "traceparent"),
            "external_opentelemetry_exporter": "NOT_OBSERVED",
            "external_collector_configured": False,
        },
        "authoritative_notes": [
            "Normal thread command execution is represented by commandExecution item lifecycle events.",
            "thread/backgroundTerminals objects expose command, cwd, itemId and processId, with optional osPid.",
            "command/exec streaming processId is connection-scoped and its schema says the server terminates the process when the originating connection closes.",
            "process/spawn returns a connection-scoped processHandle and its schema explicitly describes host execution without a Codex sandbox.",
            "thread/shellCommand is explicitly unsandboxed with full access and is excluded from all production candidates.",
        ],
        "files": files[:200],
    }


def scrub_temp(value: Any, temp: Path) -> Any:
    if isinstance(value, dict):
        return {str(key): scrub_temp(item, temp) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_temp(item, temp) for item in value]
    if isinstance(value, str):
        return value.replace(str(temp), "<schema-temp>")
    return value


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v05-schema-") as temporary:
        temp = Path(temporary)
        stable_dir = temp / "stable"
        experimental_dir = temp / "experimental"
        stable_run = run(["codex", "app-server", "generate-json-schema", "--out", str(stable_dir)])
        experimental_run = run(
            ["codex", "app-server", "generate-json-schema", "--experimental", "--out", str(experimental_dir)]
        )
        stable_path = find_schema(stable_dir)
        experimental_path = find_schema(experimental_dir)
        if experimental_path is not None:
            if SCHEMA_OUT.exists():
                shutil.rmtree(SCHEMA_OUT)
            shutil.copytree(experimental_path.parent, SCHEMA_OUT)
        summary = schema_summary(SCHEMA_OUT / experimental_path.name if experimental_path else None, stable_path=stable_path)
        summary.update(
            {
                "schema_version": 1,
                "generated_at": utc_now(),
                "codex_version": run(["codex", "--version"], timeout=20),
                "app_server_help": run(["codex", "app-server", "--help"], timeout=20),
                "stable_generation": stable_run,
                "experimental_generation": experimental_run,
                "status": "PASS" if experimental_path is not None and summary["all_method_strings_present"] else "PARTIAL" if experimental_path else "UNKNOWN",
                "thread_native_terminal": "UNKNOWN",
                "sandbox_parity": "UNKNOWN",
                "approval_parity": "UNKNOWN",
                "production_selection": "CLI_RESUME_FALLBACK",
            }
        )
        value = redact(scrub_temp(summary, temp))
    write_trace(OUT / "native-terminal-schema.json", value)
    write_trace(OUT / "native-terminal-capabilities.json", value)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
