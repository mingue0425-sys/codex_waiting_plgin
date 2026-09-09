#!/usr/bin/env python3
from __future__ import annotations

"""Measure A/B process correlation using only normal Codex thread execution."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.6"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from probes.v0_6_support import run_normal_probe
from snooze_core.models import utc_now
from tools.app_server_probe import redact, write_trace


def run(runs: int = 1) -> Dict[str, Any]:
    if runs <= 0 or runs > 30:
        raise ValueError("runs must be between 1 and 30")
    candidates: Dict[str, List[Dict[str, Any]]] = {}
    for candidate in ("THREAD_NATIVE_TERMINAL", "SANDBOX_DESCENDANT_SUPERVISOR"):
        candidates[candidate] = [run_normal_probe(candidate, run_id=index + 1) for index in range(runs)]
    summary: Dict[str, Any] = {}
    for candidate, attempts in candidates.items():
        correlations = [item.get("best_process_correlation") or {} for item in attempts]
        summary[candidate] = {
            "status": "PASS" if attempts and all(item.get("status") == "PASS" for item in attempts) else "UNKNOWN",
            "runs_requested": runs,
            "runs_completed": len(attempts),
            "sample_requirement": 30,
            "sample_requirement_met": len(attempts) >= 30,
            "normal_tool_execution_runs": sum(bool(item.get("normal_tool_execution_observed")) for item in attempts),
            "fixture_exercised_runs": sum(bool(item.get("fixture_exercised")) for item in attempts),
            "correlation_pass_runs": sum(item.get("status") == "PASS" for item in correlations),
            "correlation_fail_runs": sum(item.get("status") == "FAIL" for item in correlations),
            "correlation_unknown_runs": sum(item.get("status") not in {"PASS", "FAIL"} for item in correlations),
            "process_identity_statuses": [item.get("process_identity", "UNKNOWN") for item in attempts],
            "forbidden_method_observations": [item.get("forbidden_methods_used", []) for item in attempts],
            "attempts": attempts,
        }
    value = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "status": "PASS" if all(item["status"] == "PASS" for item in summary.values()) else "UNKNOWN",
        "runs_requested": runs,
        "sample_requirement": 30,
        "controller_candidate_execution_used": False,
        "candidates": summary,
        "notes": [
            "Only normal thread turns were used to request candidate execution.",
            "The controller did not call command/exec, process/spawn or thread/shellCommand.",
            "A command string match alone is insufficient; itemId, processId, cwd and process identity are required.",
            "A run count below 30 cannot produce a p100 handoff claim.",
        ],
    }
    return redact(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1)
    args = parser.parse_args()
    value = run(args.runs)
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "process-correlation.json", value)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
