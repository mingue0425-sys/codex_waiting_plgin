#!/usr/bin/env python3
from __future__ import annotations

"""Self-authenticating short fixture for normal Codex thread execution."""

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path


def atomic_json(path: Path, value: dict[str, object], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--result", required=False)
    args = parser.parse_args()
    nonce = str(args.nonce)
    marker_path = Path(args.marker).resolve()
    cwd = str(Path.cwd().resolve())
    pid = os.getpid()
    ppid = os.getppid()
    report = {
        "probe_version": 1,
        "nonce": nonce,
        "pid": pid,
        "ppid": ppid,
        "cwd": cwd,
        "executable": sys.executable,
        "argv": sys.argv,
        "start_monotonic_ns": time.monotonic_ns(),
        "start_identity_ns": time.monotonic_ns(),
        "marker_path": str(marker_path),
    }
    atomic_json(marker_path, {**report, "timestamp": time.time_ns()})
    if args.result:
        result_path = Path(args.result).resolve()
        token = hashlib.sha256(nonce.encode("utf-8")).hexdigest()[:24]
        atomic_json(result_path, {"nonce": nonce, "result_token": f"RESULT_TOKEN={token}", "exit_code": 0})
    # This is the only stdout record. It intentionally contains no environment
    # values or secrets, so the App Server item output can be matched exactly.
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
