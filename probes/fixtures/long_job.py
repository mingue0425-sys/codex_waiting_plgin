#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=12.0)
    parser.add_argument("--marker", required=True)
    parser.add_argument("--exit-code", type=int, default=0)
    args = parser.parse_args()
    started = time.time()
    deadline = started + max(0.0, args.duration)
    heartbeat = 0
    while time.time() < deadline:
        heartbeat += 1
        print(f"fixture-heartbeat={heartbeat}", flush=True)
        if heartbeat % 3 == 0:
            print(f"fixture-stderr={heartbeat}", file=sys.stderr, flush=True)
        time.sleep(min(1.0, max(0.01, deadline - time.time())))
    marker = Path(args.marker)
    marker.write_text(
        json.dumps({"completed": True, "exit_code": args.exit_code, "heartbeats": heartbeat}) + "\n",
        encoding="utf-8",
    )
    print("V03_LONG_JOB_COMPLETE", flush=True)
    return args.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
