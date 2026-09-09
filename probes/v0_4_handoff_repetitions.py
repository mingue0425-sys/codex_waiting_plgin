#!/usr/bin/env python3
from __future__ import annotations

"""Repeat the explicit data-plane handoff contract 20 times."""

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.4"
LAUNCHER = ROOT / "scripts" / "codex-snooze"


def run_one(index: int) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v04-repeat-") as temporary:
        root = Path(temporary)
        fixture_dir = root / "fixtures"
        fixture_dir.mkdir()
        shutil.copy2(ROOT / "probes" / "fixtures" / "handoff_long_job.py", fixture_dir / "handoff_long_job.py")
        token = f"V04_REPEAT_{index:02d}"
        command = [
            sys.executable,
            str(LAUNCHER),
            "--store",
            str(root / "store"),
            "handoff",
            "--threshold",
            "0.3",
            "--cwd",
            str(root),
            "--",
            sys.executable,
            "fixtures/handoff_long_job.py",
            "--duration",
            "0.7",
            "--run-count",
            "run_count.txt",
            "--result-token",
            "result_token.txt",
            "--result-json",
            "fixture_result.json",
            "--token",
            token,
            "--exit-code",
            "0",
        ]
        completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
        if completed.returncode != 0:
            return {"iteration": index, "status": "FAIL", "error": completed.stderr[-1000:]}
        try:
            marker = json.loads(completed.stdout)
            job_dir = root / "store" / "jobs" / marker["job_id"]
            for _ in range(100):
                if (job_dir / "result.json").exists():
                    break
                time.sleep(0.03)
            result = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
            run_count = int((root / "run_count.txt").read_text(encoding="utf-8").strip())
            # The supervisor writes result before metadata/delivery and may
            # still be closing its detached session when result.json appears.
            # Wait for the terminal metadata and a short quiet interval before
            # removing the private fixture directory.
            for _ in range(100):
                try:
                    metadata = json.loads((job_dir / "metadata.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    metadata = {}
                if metadata.get("execution_state") in {"COMPLETED", "COMPLETED_STALE", "FAILED", "CANCELLED"}:
                    break
                time.sleep(0.03)
            time.sleep(0.15)
            return {
                "iteration": index,
                "status": "PASS" if marker.get("state") == "DETACHED" and result.get("execution_state") == "COMPLETED" and result.get("exit_code") == 0 and run_count == 1 else "FAIL",
                "marker_state": marker.get("state"),
                "execution_state": result.get("execution_state"),
                "exit_code": result.get("exit_code"),
                "run_count": run_count,
            }
        except (OSError, ValueError, KeyError) as exc:
            return {"iteration": index, "status": "FAIL", "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    started = time.monotonic()
    iterations = 20
    results = [run_one(index) for index in range(1, iterations + 1)]
    value = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "iterations": iterations,
        "passed": sum(item.get("status") == "PASS" for item in results),
        "status": "PASS" if all(item.get("status") == "PASS" for item in results) else "FAIL",
        "model_backed": False,
        "results": results,
        "duration_seconds": round(time.monotonic() - started, 3),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "handoff-repetitions.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (OUT / "handoff-repetitions.md").write_text(
        "# v0.4 explicit handoff repetitions\n\n"
        f"Status: **{value['status']}**\n\n"
        f"Passed: `{value['passed']}/{value['iterations']}`\n\n"
        "These are deterministic supervisor/marker/result repetitions; the separate model-backed 10-second E2E is recorded in handoff-e2e.json.\n",
        encoding="utf-8",
    )
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
