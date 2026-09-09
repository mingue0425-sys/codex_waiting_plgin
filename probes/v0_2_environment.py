#!/usr/bin/env python3
from __future__ import annotations

"""Record the installed Codex CLI surface without assuming a documented API."""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Sequence


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.2"

COMMANDS: Dict[str, Sequence[str]] = {
    "codex_version": ("codex", "--version"),
    "codex_help": ("codex", "--help"),
    "codex_exec_help": ("codex", "exec", "--help"),
    "codex_exec_resume_help": ("codex", "exec", "resume", "--help"),
    "codex_resume_help": ("codex", "resume", "--help"),
    "codex_app_server_help": ("codex", "app-server", "--help"),
    "codex_app_server_schema_help": (
        "codex",
        "app-server",
        "generate-json-schema",
        "--help",
    ),
    "codex_app_server_ts_help": (
        "codex",
        "app-server",
        "generate-ts",
        "--help",
    ),
    "codex_debug_help": ("codex", "debug", "--help"),
    "codex_debug_app_server_help": ("codex", "debug", "app-server", "--help"),
    "codex_queue_help": ("codex", "queue", "--help"),
    "codex_agents_help": ("codex", "agents", "--help"),
    "codex_remote_control_help": ("codex", "remote-control", "--help"),
    "codex_features_help": ("codex", "features", "--help"),
    "codex_sandbox_help": ("codex", "sandbox", "--help"),
    "codex_exec_server_help": ("codex", "exec-server", "--help"),
}


def run(command: Sequence[str], timeout: float = 20.0) -> Dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(command),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=os.environ.copy(),
        )
        return {
            "argv": list(command),
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": list(command),
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": f"timeout after {timeout}s",
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    except OSError as exc:
        return {
            "argv": list(command),
            "returncode": 127,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "duration_seconds": round(time.monotonic() - started, 6),
        }


def parse_version(text: str) -> str | None:
    match = re.search(r"codex-cli\s+([^\s]+)", text)
    return match.group(1) if match else None


def parse_help(text: str) -> Dict[str, List[str]]:
    commands: List[str] = []
    options: List[str] = []
    for line in text.splitlines():
        command_match = re.match(r"^\s{2}([a-z][a-z0-9-]*)\s{2,}", line)
        if command_match and command_match.group(1) not in {"Usage", "Options", "Arguments"}:
            commands.append(command_match.group(1))
        option_match = re.match(r"^\s{2,}(-{1,2}[a-zA-Z0-9][^\s,]*)", line)
        if option_match:
            options.append(option_match.group(1))
    return {"commands": sorted(set(commands)), "options": sorted(set(options))}


def build_payload() -> Dict[str, Any]:
    command_results = {name: run(command) for name, command in COMMANDS.items()}
    help_parsed = {
        name: parse_help(value["stdout"])
        for name, value in command_results.items()
        if name != "codex_version"
    }
    version_output = command_results["codex_version"]["stdout"]
    successful = [value["returncode"] == 0 for value in command_results.values()]
    return {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": {"system": os.uname().sysname, "release": os.uname().release, "machine": os.uname().machine},
        "shell": os.environ.get("SHELL", "UNKNOWN"),
        "codex_path": shutil.which("codex"),
        "codex_version": parse_version(version_output),
        "commands": command_results,
        "parsed_help": help_parsed,
        "status": "PASS" if all(successful) else "PARTIAL" if any(successful) else "FAIL",
        "authority": "installed binary and runtime probes; names are not inferred from documentation",
    }


def write_payload(payload: Dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    json_path = OUT / "codex-environment.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Codex environment (v0.2)",
        "",
        f"Generated: `{payload['generated_at']}`",
        f"Codex: `{payload.get('codex_version') or 'UNKNOWN'}`",
        f"Binary: `{payload.get('codex_path') or 'UNKNOWN'}`",
        f"Platform: `{payload['platform']['system']} {payload['platform']['machine']}`",
        f"Status: **{payload['status']}**",
        "",
        "The installed binary is authoritative for this probe. Help output is",
        "stored verbatim in the JSON so protocol names can be audited later.",
        "",
        "| Probe | Return code | Duration |",
        "|---|---:|---:|",
    ]
    for name, result in payload["commands"].items():
        lines.append(f"| `{name}` | {result['returncode']} | {result['duration_seconds']} s |")
    lines.extend(["", "## Parsed commands and options", ""])
    for name, parsed in payload["parsed_help"].items():
        lines.append(f"### `{name}`")
        lines.append("")
        lines.append(f"- commands: `{', '.join(parsed['commands']) or 'none'}`")
        lines.append(f"- options: `{', '.join(parsed['options']) or 'none'}`")
        lines.append("")
    (OUT / "codex-environment.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    payload = build_payload()
    write_payload(payload)
    print(json.dumps({"status": payload["status"], "codex_version": payload["codex_version"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
