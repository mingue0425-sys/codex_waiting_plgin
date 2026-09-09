#!/usr/bin/env python3
from __future__ import annotations

"""Bounded long-running fixture used only by a normal Codex terminal call."""

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path


def atomic_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--run-count", required=True)
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--exit-code", type=int, default=0)
    args = parser.parse_args()
    nonce = str(args.nonce)
    root = Path.cwd().resolve()
    marker_path = Path(args.marker).resolve()
    result_path = Path(args.result).resolve()
    count_path = Path(args.run_count).resolve()
    pid = os.getpid()
    ppid = os.getppid()
    try:
        count = int(count_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        count = 0
    count += 1
    count_path.write_text(str(count), encoding="utf-8")
    report = {
        "probe_version": 1,
        "nonce": nonce,
        "pid": pid,
        "ppid": ppid,
        "cwd": str(root),
        "executable": sys.executable,
        "argv": sys.argv,
        "start_monotonic_ns": time.monotonic_ns(),
        "start_identity_ns": time.monotonic_ns(),
        "marker_path": str(marker_path),
        "run_count": count,
    }
    atomic_json(marker_path, {**report, "timestamp": time.time_ns()})
    print(json.dumps(report, sort_keys=True), flush=True)
    deadline = time.monotonic() + max(0.2, float(args.duration))
    heartbeat = 0
    while time.monotonic() < deadline:
        heartbeat += 1
        (root / "heartbeat.json").write_text(
            json.dumps({"nonce": nonce, "pid": pid, "heartbeat": heartbeat, "timestamp": time.time_ns()}, sort_keys=True),
            encoding="utf-8",
        )
        time.sleep(0.25)
    token = hashlib.sha256(nonce.encode("utf-8")).hexdigest()[:24]
    atomic_json(
        result_path,
        {
            "nonce": nonce,
            "pid": pid,
            "run_count": count,
            "heartbeat": heartbeat,
            "result_token": f"RESULT_TOKEN={token}",
            "exit_code": int(args.exit_code),
            "completed_monotonic_ns": time.monotonic_ns(),
        },
    )
    print(json.dumps({"nonce": nonce, "result_token": f"RESULT_TOKEN={token}", "exit_code": int(args.exit_code)}), flush=True)
    return int(args.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
