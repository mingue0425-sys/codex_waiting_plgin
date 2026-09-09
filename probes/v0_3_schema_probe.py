#!/usr/bin/env python3
from __future__ import annotations

"""Capture the installed Codex App Server schema/TS fixtures for v0.3."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.3" / "app-server-schema"


def run(argv: List[str], timeout: float = 90.0) -> Dict[str, Any]:
    started = time.monotonic()
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
            "stdout": result.stdout[-12000:],
            "stderr": result.stderr[-12000:],
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "argv": argv,
            "returncode": 124 if isinstance(exc, subprocess.TimeoutExpired) else 127,
            "stdout": "",
            "stderr": str(exc),
            "duration_seconds": round(time.monotonic() - started, 6),
        }


def files(directory: Path, suffix: str) -> List[str]:
    return sorted(str(path.relative_to(OUT)) for path in directory.rglob(f"*{suffix}") if path.is_file())


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    stable_json = OUT / "stable-json"
    experimental_json = OUT / "experimental-json"
    stable_ts = OUT / "stable-ts"
    experimental_ts = OUT / "experimental-ts"
    runs = {
        "codex_version": run(["codex", "--version"], timeout=15),
        "app_server_help": run(["codex", "app-server", "--help"], timeout=15),
        "stable_json": run(["codex", "app-server", "generate-json-schema", "--out", str(stable_json)]),
        "experimental_json": run(
            ["codex", "app-server", "generate-json-schema", "--experimental", "--out", str(experimental_json)]
        ),
        "stable_ts": run(["codex", "app-server", "generate-ts", "--out", str(stable_ts)]),
        "experimental_ts": run(
            ["codex", "app-server", "generate-ts", "--experimental", "--out", str(experimental_ts)]
        ),
    }
    inventory = {
        "stable_json_files": files(stable_json, ".json"),
        "experimental_json_files": files(experimental_json, ".json"),
        "stable_ts_files": files(stable_ts, ".ts"),
        "experimental_ts_files": files(experimental_ts, ".ts"),
    }
    for key, directory in (
        ("stable_json_sha256", stable_json),
        ("experimental_json_sha256", experimental_json),
        ("stable_ts_sha256", stable_ts),
        ("experimental_ts_sha256", experimental_ts),
    ):
        digest = hashlib.sha256()
        for path in sorted(directory.rglob("*")) if directory.exists() else []:
            if path.is_file():
                digest.update(path.relative_to(directory).as_posix().encode())
                digest.update(path.read_bytes())
        inventory[key] = digest.hexdigest() if digest.digest_size else None
    value = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "codex_version": runs["codex_version"]["stdout"].strip(),
        "runs": runs,
        "inventory": inventory,
        "required_files": {
            "initialize": any("InitializeParams.json" in item for item in inventory["experimental_json_files"]),
            "thread_start": any("ThreadStartParams.json" in item for item in inventory["experimental_json_files"]),
            "turn_start": any("TurnStartParams.json" in item for item in inventory["experimental_json_files"]),
            "background_terminals": any(
                "ThreadBackgroundTerminalsListParams.json" in item for item in inventory["experimental_json_files"]
            ),
        },
        "status": "PASS"
        if all(runs[name]["returncode"] == 0 for name in ("codex_version", "app_server_help", "stable_json", "experimental_json"))
        else "PARTIAL",
    }
    (OUT.parent / "schema-probe.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# v0.3 App Server schema fixture",
        "",
        f"Generated: `{value['generated_at']}`",
        f"Status: **{value['status']}**",
        f"Codex: `{value['codex_version']}`",
        "",
        "The installed stable and experimental JSON/TypeScript outputs are",
        "stored below `results/v0.3/app-server-schema/`. They are fixtures for",
        "the owned process adapter; no Desktop transport is inferred from them.",
        "",
        f"Stable JSON files: `{len(inventory['stable_json_files'])}`",
        f"Experimental JSON files: `{len(inventory['experimental_json_files'])}`",
        f"Stable TypeScript files: `{len(inventory['stable_ts_files'])}`",
        f"Experimental TypeScript files: `{len(inventory['experimental_ts_files'])}`",
        "",
        "| Required family | Present |",
        "|---|---|",
    ]
    for name, present in value["required_files"].items():
        lines.append(f"| `{name}` | `{present}` |")
    (OUT.parent / "schema-probe.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value["status"] in {"PASS", "PARTIAL"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
