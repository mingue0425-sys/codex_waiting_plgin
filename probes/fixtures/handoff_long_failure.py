#!/usr/bin/env python3
from __future__ import annotations

"""Failure variant of the v0.4 long-job fixture; the final exit is 7."""

import sys
from pathlib import Path

from handoff_long_job import main as _long_main


if __name__ == "__main__":
    # Keep the failure contract in a separate executable fixture while
    # reusing the same heartbeat, run counter and result-token behavior.
    if "--exit-code" not in sys.argv:
        sys.argv.extend(["--exit-code", "7"])
    raise SystemExit(_long_main())
