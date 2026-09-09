#!/usr/bin/env python3
from __future__ import annotations

"""Exercise App Server turn interruption around a detached Snooze handoff.

The model-backed path is opt-in.  Every turn uses a disposable App Server
thread and a temporary store; a missing model tool invocation is recorded as
UNKNOWN rather than being treated as a supervisor failure.
"""

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.app_server_probe import AppServerClient, redact, response_error, write_trace


OUT = ROOT / "results" / "v0.2"
RACE_POINTS: Dict[str, Dict[str, float]] = {
    "child_start": {"after_running": 0.03},
    "threshold_before": {"after_running": 0.45},
    "threshold_after": {"after_running": 1.00},
    "command_end": {"after_running": 1.85},
    "simultaneous": {"after_running": 0.80},
}


def response_summary(response: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if response is None:
        return {"error": "no response"}
    return {"error": response_error(response), "response": redact(response)}


def _turn_id(response: Dict[str, Any]) -> Optional[str]:
    turn = ((response.get("result") or {}).get("turn") or {})
    return turn.get("id") if isinstance(turn, dict) else None


def _wait_for_job(store: Path, timeout: float) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jobs = store / "jobs"
        if jobs.is_dir():
            candidates = sorted(entry for entry in jobs.iterdir() if entry.is_dir())
            if candidates:
                job_id = candidates[0].name
                metadata_path = candidates[0] / "metadata.json"
                if metadata_path.exists():
                    try:
                        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        metadata = None
                    if isinstance(metadata, dict) and metadata.get("execution_state") == "RUNNING":
                        return job_id, metadata
        time.sleep(0.05)
    return None, None


def _wait_for_result(store: Path, job_id: str, timeout: float) -> Optional[Dict[str, Any]]:
    path = store / "jobs" / job_id / "result.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                value = None
            if isinstance(value, dict):
                return value
        time.sleep(0.1)
    return None


def _wait_turn_completed(client: AppServerClient, turn_id: str, timeout: float) -> Optional[Dict[str, Any]]:
    return client.wait_for_notification(
        lambda item: item.get("method") == "turn/completed"
        and ((item.get("params") or {}).get("turn") or {}).get("id") == turn_id,
        timeout=timeout,
    )


def _launcher(store: Path, marker: Path, handoff_after: float) -> str:
    inner = (
        f"printf CHILD_STARTED > {shlex.quote(str(marker))}; "
        "sleep 2; "
        f"printf CHILD_COMPLETE > {shlex.quote(str(marker))}"
    )
    return (
        f"cd {shlex.quote(str(ROOT))} && "
        f"PYTHONPATH={shlex.quote(str(ROOT))} python3 -m snooze_core "
        f"--store {shlex.quote(str(store))} submit --shell /bin/bash "
        f"--handoff-after {handoff_after} --command {shlex.quote(inner)}"
    )


def _cleanup_job(store: Path, job_id: Optional[str]) -> None:
    if not job_id:
        return
    try:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "snooze_core",
                "--store",
                str(store),
                "cancel",
                job_id,
                "--grace-seconds",
                "0.5",
            ],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")},
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def one_run(race_point: str, run_number: int, timeout: float, live: bool) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "race_point": race_point,
        "run_number": run_number,
        "live_requested": live,
        "status": "UNKNOWN",
    }
    if not live:
        result["reason"] = "live model probe not requested"
        return result

    temporary = Path(tempfile.mkdtemp(prefix=f"codex-snooze-v02-{race_point}-"))
    store = temporary / "store"
    marker = temporary / "marker"
    client = AppServerClient(cwd=ROOT)
    thread_id: Optional[str] = None
    turn_id: Optional[str] = None
    job_id: Optional[str] = None
    try:
        initialized = client.request(
            "initialize",
            {
                "clientInfo": {"name": "codex-snooze-v02-interrupt", "version": "0.2.0"},
                "capabilities": {"experimentalApi": True, "requestAttestation": False},
            },
            timeout=20,
        )
        result["initialize"] = response_summary(initialized)
        if response_error(initialized):
            result["reason"] = "app-server initialize failed"
            return result
        client.notify("initialized", {})
        started = client.request("thread/start", {"cwd": str(ROOT), "ephemeral": False}, timeout=20)
        result["thread_start"] = response_summary(started)
        thread_id = ((started.get("result") or {}).get("thread") or {}).get("id")
        if not thread_id:
            result["reason"] = "disposable thread was not created"
            return result
        command = _launcher(store, marker, 0.8)
        turn = client.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [
                    {
                        "type": "text",
                        "text": (
                            "Use the terminal tool immediately. Run exactly the following command, wait for its "
                            "completion, and do not answer first.\n\nCOMMAND:\n" + command
                        ),
                    }
                ],
            },
            timeout=30,
        )
        result["turn_start"] = response_summary(turn)
        turn_id = _turn_id(turn)
        if not turn_id:
            result["reason"] = "turn id was not returned"
            return result
        job_id, metadata = _wait_for_job(store, timeout=min(timeout, 20.0))
        result["job_id"] = job_id
        result["metadata_before_interrupt"] = redact(metadata)
        if job_id is None:
            completed = _wait_for_result(store, "missing", 0.1)
            result["turn_completed_without_job"] = True
            result["status"] = "UNKNOWN"
            result["reason"] = "model turn did not invoke the requested terminal command"
            return result

        delay = RACE_POINTS[race_point]["after_running"]
        time.sleep(delay)
        interrupt_started = time.monotonic()
        interrupt = client.request(
            "turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=30
        )
        result["interrupt"] = response_summary(interrupt)
        result["interrupt_after_running_seconds"] = round(time.monotonic() - interrupt_started + delay, 6)
        completed = _wait_turn_completed(client, turn_id, timeout=45)
        result["turn_completed"] = redact(completed)
        result["turn_completed_status"] = (
            ((completed or {}).get("params") or {}).get("turn") or {}
        ).get("status")
        final = _wait_for_result(store, job_id, timeout=10)
        marker_text = marker.read_text(encoding="utf-8", errors="replace") if marker.exists() else ""
        result["result"] = redact(final)
        result["marker_written"] = marker.exists()
        result["marker_complete"] = "CHILD_COMPLETE" in marker_text
        result["supervisor_survival_evidence"] = bool(final and final.get("completion_event_id"))
        result["terminal_notifications"] = [
            item.get("method")
            for item in client.notifications
            if item.get("method") in {"item/started", "item/completed", "turn/completed"}
        ]
        good = (
            response_error(interrupt) is None
            and result["turn_completed_status"] == "interrupted"
            and isinstance(final, dict)
            and final.get("exit_code") == 0
            and result["marker_complete"]
            and result["supervisor_survival_evidence"]
        )
        result["status"] = "PASS" if good else "PARTIAL"
        if not good:
            result["reason"] = "one or more handoff invariants were not observed"
        return result
    except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
        result["status"] = "UNKNOWN"
        result["reason"] = f"{type(exc).__name__}: {exc}"
        return result
    finally:
        result["notification_count"] = len(client.notifications)
        result["trace"] = client.snapshot()
        if job_id:
            final_path = store / "jobs" / job_id / "result.json"
            if not final_path.exists():
                _cleanup_job(store, job_id)
        if turn_id and thread_id:
            try:
                client.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=5)
            except (OSError, RuntimeError, TimeoutError, ValueError):
                pass
        if thread_id:
            try:
                client.request("thread/delete", {"threadId": thread_id}, timeout=10)
            except (OSError, RuntimeError, TimeoutError, ValueError):
                pass
        client.close()
        shutil.rmtree(temporary, ignore_errors=True)


def aggregate(runs: List[Dict[str, Any]], live: bool) -> str:
    if not live or not runs:
        return "UNKNOWN"
    statuses = [run.get("status") for run in runs]
    if statuses and all(status == "PASS" for status in statuses):
        return "PASS"
    if any(status in {"PASS", "PARTIAL"} for status in statuses):
        return "PARTIAL"
    return "UNKNOWN"


def build_payload(live: bool, repetitions: int, timeout: float) -> Dict[str, Any]:
    runs: List[Dict[str, Any]] = []
    for race_point in RACE_POINTS:
        for run_number in range(1, repetitions + 1):
            runs.append(one_run(race_point, run_number, timeout, live))
    by_point = {
        race_point: [run for run in runs if run["race_point"] == race_point]
        for race_point in RACE_POINTS
    }
    return {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "live_requested": live,
        "repetitions_per_race_point": repetitions,
        "timeout_seconds": timeout,
        "race_points": list(RACE_POINTS),
        "runs": runs,
        "by_race_point": by_point,
        "aggregate_status": aggregate(runs, live),
        "handoff_invariants": {
            "turn_interrupted": "PASS only when turn/completed.status=interrupted",
            "supervisor_survival": "PASS only when durable result with completion event exists",
            "actual_command_continues": "PASS only when deterministic marker reaches CHILD_COMPLETE",
            "exit_code_authoritative": "PASS only when result.exit_code=0",
        },
    }


def write_outputs(payload: Dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "interrupt-tests.json", payload)
    lines = [
        "# v0.2 interrupt and handoff races",
        "",
        f"Generated: `{payload['generated_at']}`",
        f"Live model probe: `{payload['live_requested']}`",
        f"Repetitions per race point: `{payload['repetitions_per_race_point']}`",
        f"Aggregate: **{payload['aggregate_status']}**",
        "",
        "| Race point | Runs | PASS | PARTIAL | UNKNOWN |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, runs in payload["by_race_point"].items():
        counts = {status: sum(run.get("status") == status for run in runs) for status in ("PASS", "PARTIAL", "UNKNOWN", "FAIL")}
        lines.append(f"| `{name}` | {len(runs)} | {counts['PASS']} | {counts['PARTIAL']} | {counts['UNKNOWN']} |")
    lines.extend(["", "## Invariants", ""])
    for name, meaning in payload["handoff_invariants"].items():
        lines.append(f"- `{name}`: {meaning}")
    lines.extend(["", "Full request/response/notification traces are embedded in `interrupt-tests.json`.", ""])
    (OUT / "interrupt-tests.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Probe Codex turn interrupt and Snooze handoff races")
    parser.add_argument("--live", action="store_true", help="run model-backed disposable-thread probes")
    parser.add_argument("--runs", type=int, default=1, help="repetitions for each race point")
    parser.add_argument("--timeout", type=float, default=60.0, help="per-run job discovery timeout")
    args = parser.parse_args(argv)
    if args.runs <= 0 or args.timeout <= 0:
        parser.error("--runs and --timeout must be positive")
    payload = build_payload(args.live, args.runs, args.timeout)
    write_outputs(payload)
    print(json.dumps({"aggregate_status": payload["aggregate_status"], "runs": len(payload["runs"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
