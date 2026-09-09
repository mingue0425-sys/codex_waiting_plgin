#!/usr/bin/env python3
from __future__ import annotations

"""Deterministic, non-interactive long-job fixture for v0.4 handoff tests."""

import argparse
import json
import os
import sys
import time
from pathlib import Path


def _increment(path: Path) -> int:
    current = int(path.read_text(encoding="utf-8").strip()) if path.exists() else 0
    current += 1
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(f"{current}\n", encoding="utf-8")
    os.replace(temporary, path)
    return current


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=15.0)
    parser.add_argument("--run-count", type=Path, required=True)
    parser.add_argument("--result-token", type=Path, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--result-json", type=Path)
    parser.add_argument("--exit-code", type=int, default=0)
    args = parser.parse_args()
    if args.duration < 0 or not args.token:
        raise SystemExit(2)
    run_count = _increment(args.run_count)
    print(f"HANDOFF_FIXTURE_START run_count={run_count}", flush=True)
    start = time.monotonic()
    next_heartbeat = 0.0
    while time.monotonic() - start < args.duration:
        elapsed = time.monotonic() - start
        if elapsed >= next_heartbeat:
            print(f"HANDOFF_HEARTBEAT elapsed={elapsed:.2f}", flush=True)
            print(f"HANDOFF_DIAGNOSTIC elapsed={elapsed:.2f}", file=sys.stderr, flush=True)
            next_heartbeat += 1.0
        time.sleep(0.05)
    args.result_token.write_text(args.token + "\n", encoding="utf-8")
    if args.result_json is not None:
        args.result_json.write_text(
            json.dumps({"result_token": args.token, "run_count": run_count, "exit_code": args.exit_code}) + "\n",
            encoding="utf-8",
        )
    print(f"HANDOFF_FIXTURE_COMPLETE RESULT_TOKEN={args.token} run_count={run_count}", flush=True)
    return args.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
