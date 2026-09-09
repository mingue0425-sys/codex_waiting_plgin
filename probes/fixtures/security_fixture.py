#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def attempt(path: Path, payload: str) -> dict:
    try:
        path.write_text(payload, encoding="utf-8")
        return {"ok": True, "error": None}
    except OSError as exc:
        return {"ok": False, "error": type(exc).__name__}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inside", required=True)
    parser.add_argument("--outside", required=True)
    args = parser.parse_args()
    inside = attempt(Path(args.inside), "inside\n")
    outside = attempt(Path(args.outside), "outside\n")
    print(json.dumps({"inside": inside, "outside": outside}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
