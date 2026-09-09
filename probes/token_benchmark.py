#!/usr/bin/env python3
"""Measure local baseline versus Snooze handoff without inventing token data."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


def run_baseline(duration: float) -> Dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(
        [sys.executable, "-c", f"import time; time.sleep({duration!r})"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=max(10.0, duration + 5.0),
        check=False,
    )
    return {
        "returncode": completed.returncode,
        "wall_seconds": round(time.monotonic() - started, 6),
        "input_tokens": None,
        "output_tokens": None,
        "cached_tokens": None,
        "model_turns": None,
        "tool_calls": None,
        "poll_calls": None,
    }


def run_snooze(duration: float) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-benchmark-") as temporary:
        store = Path(temporary) / "store"
        command = f"sleep {duration!r}; printf TOKEN_BENCHMARK_COMPLETE"
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
        foreground_started = time.monotonic()
        submitted = subprocess.run(
            [
                sys.executable,
                "-m",
                "snooze_core",
                "--store",
                str(store),
                "submit",
                "--shell",
                "/bin/bash",
                "--handoff-after",
                str(min(0.1, max(0.01, duration / 3))),
                "--command",
                command,
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=max(10.0, duration + 5.0),
            check=False,
        )
        foreground_seconds = time.monotonic() - foreground_started
        if submitted.returncode != 0:
            return {
                "submit_returncode": submitted.returncode,
                "foreground_seconds": round(foreground_seconds, 6),
                "completion_wall_seconds": None,
                "local_result_checks": 0,
                "input_tokens": None,
                "output_tokens": None,
                "cached_tokens": None,
                "model_turns": None,
                "tool_calls": None,
                "poll_calls": None,
            }
        handoff = json.loads(submitted.stdout)
        job_id = handoff["job_id"]
        result_path = store / "jobs" / job_id / "result.json"
        checks = 0
        completed_at = None
        deadline = time.monotonic() + max(10.0, duration + 5.0)
        while time.monotonic() < deadline:
            checks += 1
            if result_path.exists():
                completed_at = time.monotonic()
                break
            time.sleep(0.05)
        return {
            "submit_returncode": submitted.returncode,
            "foreground_seconds": round(foreground_seconds, 6),
            "completion_wall_seconds": round(completed_at - foreground_started, 6)
            if completed_at is not None
            else None,
            "local_result_checks": checks,
            "input_tokens": None,
            "output_tokens": None,
            "cached_tokens": None,
            "model_turns": None,
            "tool_calls": None,
            "poll_calls": None,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local Snooze token benchmark")
    parser.add_argument("--duration", type=float, default=0.4)
    args = parser.parse_args(argv)
    if args.duration <= 0:
        parser.error("--duration must be positive")
    payload = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "workload": {"type": "sleep_then_marker", "duration_seconds": args.duration},
        "baseline": run_baseline(args.duration),
        "snooze": run_snooze(args.duration),
        "interpretation": (
            "Local timing is measured. Codex token, turn and tool telemetry is not exposed by this harness, "
            "so those fields remain null; no token savings claim is inferred."
        ),
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "token-benchmark.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    baseline = payload["baseline"]
    snooze = payload["snooze"]
    markdown = "\n".join(
        [
            "# Token benchmark",
            "",
            f"Generated: `{payload['generated_at']}`",
            "",
            "| Workload | Foreground/completion wall time | Token telemetry |",
            "|---|---:|---|",
            f"| baseline | {baseline['wall_seconds']} s | unavailable (`null`) |",
            f"| snooze foreground return | {snooze['foreground_seconds']} s | unavailable (`null`) |",
            f"| snooze completion | {snooze['completion_wall_seconds']} s | unavailable (`null`) |",
            "",
            payload["interpretation"],
            "",
        ]
    )
    (RESULTS / "token-benchmark.md").write_text(markdown, encoding="utf-8")
    print(json.dumps({"baseline": baseline, "snooze": snooze}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
