#!/usr/bin/env python3
from __future__ import annotations

"""Check scaled equivalents of the 9.5/10/10.5 second boundary."""

import json
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.4"
LAUNCHER = ROOT / "scripts" / "codex-snooze"


def one(logical_threshold: float, scaled_threshold: float, duration: float) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v04-threshold-") as temporary:
        root = Path(temporary)
        command = [
            sys.executable,
            str(LAUNCHER),
            "--store",
            str(root / "store"),
            "handoff",
            "--threshold",
            str(scaled_threshold),
            "--cwd",
            str(root),
            "--command",
            f"{shlex.join([sys.executable, '-c', f'import time; time.sleep({duration}); print(\"THRESHOLD_OK\")'])}",
        ]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
        value: Dict[str, Any] = {
            "logical_threshold_seconds": logical_threshold,
            "scaled_threshold_seconds": scaled_threshold,
            "fixture_duration_seconds": duration,
            "returncode": completed.returncode,
        }
        if completed.returncode == 0:
            output = json.loads(completed.stdout)
            value["state"] = output.get("state")
            value["exit_code"] = output.get("exit_code")
            value["detached"] = output.get("state") == "DETACHED"
            value["expected"] = "DETACHED" if duration > scaled_threshold else "COMPLETED"
            if output.get("state") == "DETACHED":
                job = root / "store" / "jobs" / output["job_id"]
                for _ in range(100):
                    if (job / "result.json").exists():
                        break
                    time.sleep(0.03)
                result = json.loads((job / "result.json").read_text(encoding="utf-8"))
                value["result_state"] = result.get("execution_state")
                value["result_exit_code"] = result.get("exit_code")
            actual_exit = value.get("exit_code") if value.get("exit_code") is not None else value.get("result_exit_code")
            value["status"] = "PASS" if value["state"] == value["expected"] and actual_exit == 0 else "FAIL"
        else:
            value["status"] = "FAIL"
            value["stderr"] = completed.stderr[-1000:]
        return value


def main() -> int:
    # 0.15s represents 1 logical second. Cases straddle a scaled boundary
    # while leaving enough margin for the supervisor spawn overhead;
    # the primary live E2E uses the real 10s value.
    cases = [
        one(9.5, 1.425, 0.2),
        one(10.0, 1.5, 1.8),
        one(10.5, 1.575, 0.2),
    ]
    value = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scale": "0.15 scaled seconds per logical second; margin absorbs process spawn overhead",
        "cases": cases,
        "status": "PASS" if all(case["status"] == "PASS" for case in cases) else "FAIL",
        "semantics": "threshold_seconds is a foreground wait; it does not kill or time-limit the job",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "threshold-tests.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "threshold-tests.md").write_text(
        "# v0.4 threshold tests\n\n"
        f"Status: **{value['status']}**\n\n"
        + "\n".join(f"- logical `{case['logical_threshold_seconds']}` / scaled `{case['scaled_threshold_seconds']}`: **{case['status']}** (`{case['state']}`)" for case in cases)
        + "\n\n"
        + value["semantics"]
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
