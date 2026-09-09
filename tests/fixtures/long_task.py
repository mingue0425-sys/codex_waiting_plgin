#!/usr/bin/env python3
"""Thirty-second non-interactive task used by the first supervisor PoC."""

from __future__ import annotations

import sys
import time


for second in range(1, 31):
    print(f"stdout second={second}", flush=True)
    if second % 5 == 0:
        print(f"stderr second={second}", file=sys.stderr, flush=True)
    time.sleep(1)

print("SNOOZE_POC_COMPLETE", flush=True)
