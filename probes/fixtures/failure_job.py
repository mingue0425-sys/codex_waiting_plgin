#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=float, default=1.0)
    args = parser.parse_args()
    print("V03_FAILURE_FIXTURE_START", flush=True)
    time.sleep(max(0.0, args.duration))
    print("V03_FAILURE_FIXTURE_EXIT_7", file=sys.stderr, flush=True)
    return 7


if __name__ == "__main__":
    raise SystemExit(main())
