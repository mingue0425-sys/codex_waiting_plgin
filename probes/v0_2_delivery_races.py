#!/usr/bin/env python3
from __future__ import annotations

"""Run deterministic delivery crash and concurrency experiments.

These cases exercise the v0.1 outbox without sending anything to a real Codex
thread.  Codex message and turn counts are therefore explicitly marked as
NOT_ATTEMPTED; the result is evidence about durable delivery state only.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.controller import DeliveryController
from snooze_core.models import DeliveryState, JobSpec
from snooze_core.recovery import recover_store
from snooze_core.store import JobStore
from tools.app_server_probe import write_trace


OUT = ROOT / "results" / "v0.2"


def env_with(**changes: str) -> Dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
    environment.update(changes)
    return environment


def complete_job(temporary: Path, command: str = "printf V02_DELIVERY") -> tuple[JobStore, str]:
    store = JobStore(temporary / "store")
    spec = JobSpec.create(command=command, cwd=ROOT, shell="/bin/bash")
    store.create(spec)
    completed = subprocess.run(
        [sys.executable, "-m", "snooze_core.supervisor", "--run", spec.job_id, "--store", str(store.root)],
        cwd=ROOT,
        env=env_with(),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"fixture supervisor failed: {completed.returncode}: {completed.stderr}")
    return store, spec.job_id


def cli(store: JobStore, args: List[str], environment: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "snooze_core", "--store", str(store.root), *args],
        cwd=ROOT,
        env=environment or env_with(),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def result(name: str, status: str, **observations: Any) -> Dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "observations": {
            "codex_messages": "NOT_ATTEMPTED",
            "codex_turns": "NOT_ATTEMPTED",
            **observations,
        },
    }


def case_duplicate_and_retry() -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v02-delivery-") as temporary_name:
        store, job_id = complete_job(Path(temporary_name))
        controller = DeliveryController(store)
        first = controller.deliver_manual(job_id)
        duplicate_without_permission = controller.deliver_manual(job_id)
        try:
            controller.retry(job_id)
            retry_without_permission = "unexpectedly_allowed"
        except Exception as exc:  # expected explicit duplicate guard
            retry_without_permission = type(exc).__name__
        controller.acknowledge(job_id, event_id=first["event_id"])
        acked_delivery = store.read_delivery(job_id)
        after_ack = controller.deliver_manual(job_id)
        return result(
            "J1_duplicate_event_and_ack_guard",
            "PASS"
            if first["state"] == DeliveryState.SENT_UNCONFIRMED.value
            and duplicate_without_permission.get("action") == "ack_or_explicitly_retry"
            and retry_without_permission != "unexpectedly_allowed"
            and acked_delivery["state"] == DeliveryState.ACKED.value
            and after_ack["state"] == DeliveryState.ACKED.value
            else "FAIL",
            first=first,
            duplicate_without_permission=duplicate_without_permission,
            retry_without_permission=retry_without_permission,
            after_ack=after_ack,
        )


def case_delivery_crash(point: str) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"codex-snooze-v02-{point}-") as temporary_name:
        store, job_id = complete_job(Path(temporary_name))
        crashed = cli(
            store,
            ["deliver", job_id],
            env_with(
                SNOOZE_FAULT_POINT=point,
                SNOOZE_FAULT_TARGET="delivery_payload.json",
            ),
        )
        before = store.read_delivery(job_id)
        recovered = recover_store(store.root, job_id)[0]
        after = store.read_delivery(job_id)
        expected = (
            DeliveryState.SENT_UNCONFIRMED.value if point == "after_rename" else DeliveryState.RETRY_WAIT.value
        )
        return result(
            f"J2_delivery_controller_crash_{point}",
            "PASS" if crashed.returncode == 75 and after["state"] == expected else "FAIL",
            crash_returncode=crashed.returncode,
            delivery_before_recovery=before,
            recovery=recovered,
            delivery_after_recovery=after,
        )


def case_ack_crash() -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v02-ack-") as temporary_name:
        store, job_id = complete_job(Path(temporary_name))
        sent = DeliveryController(store).deliver_manual(job_id)
        crashed = cli(
            store,
            ["ack", job_id, "--event-id", sent["event_id"]],
            env_with(SNOOZE_FAULT_POINT="after_rename", SNOOZE_FAULT_TARGET="delivery.json"),
        )
        recovered = recover_store(store.root, job_id)[0]
        after = store.read_delivery(job_id)
        return result(
            "J3_ack_write_crash_after_rename",
            "PASS" if crashed.returncode == 75 and after["state"] == DeliveryState.ACKED.value else "FAIL",
            crash_returncode=crashed.returncode,
            recovery=recovered,
            delivery_after_recovery=after,
        )


def case_retry_after_restart() -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v02-restart-") as temporary_name:
        store, job_id = complete_job(Path(temporary_name))
        first = DeliveryController(store).deliver_manual(job_id)
        recovered = recover_store(store.root, job_id)[0]
        controller_after_restart = DeliveryController(JobStore(store.root))
        retry = controller_after_restart.retry(job_id, allow_duplicate=True)
        second = controller_after_restart.deliver_manual(job_id)
        return result(
            "J4_controller_restart_explicit_duplicate_retry",
            "PASS"
            if first["event_id"] == second["event_id"]
            and retry["state"] == DeliveryState.PENDING.value
            and second["state"] == DeliveryState.SENT_UNCONFIRMED.value
            and second["attempts"] == first["attempts"] + 1
            else "FAIL",
            first=first,
            recovery=recovered,
            retry=retry,
            second=second,
        )


def case_concurrent_claim() -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v02-concurrent-") as temporary_name:
        store, job_id = complete_job(Path(temporary_name))
        environment = env_with(
            SNOOZE_FAULT_POINT="after_temp_fsync",
            SNOOZE_FAULT_TARGET="delivery_payload.json",
        )
        first = subprocess.Popen(
            [sys.executable, "-m", "snooze_core", "--store", str(store.root), "deliver", job_id],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        second = subprocess.Popen(
            [sys.executable, "-m", "snooze_core", "--store", str(store.root), "deliver", job_id],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        first_output, first_error = first.communicate(timeout=10)
        second_output, second_error = second.communicate(timeout=10)
        before = store.read_delivery(job_id)
        recovered = recover_store(store.root, job_id)[0]
        after = store.read_delivery(job_id)
        attempts = int(after.get("attempts", 0))
        return result(
            "J5_concurrent_delivery_claim",
            "PASS"
            if attempts == 1 and before["state"] in {
                DeliveryState.CLAIMED.value,
                DeliveryState.SENDING.value,
                DeliveryState.RETRY_WAIT.value,
            }
            and after["state"] == DeliveryState.RETRY_WAIT.value
            else "FAIL",
            first_returncode=first.returncode,
            second_returncode=second.returncode,
            first_stdout=first_output,
            first_stderr=first_error,
            second_stdout=second_output,
            second_stderr=second_error,
            delivery_before_recovery=before,
            recovery=recovered,
            delivery_after_recovery=after,
        )


def build_payload(repetitions: int = 1) -> Dict[str, Any]:
    if repetitions <= 0:
        raise ValueError("repetitions must be positive")
    cases = []
    for run_number in range(1, repetitions + 1):
        run_cases = [
            case_duplicate_and_retry(),
            case_delivery_crash("before_rename"),
            case_delivery_crash("after_rename"),
            case_ack_crash(),
            case_retry_after_restart(),
            case_concurrent_claim(),
        ]
        for case in run_cases:
            case["run_number"] = run_number
        cases.extend(run_cases)
    statuses = [case["status"] for case in cases]
    return {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repetitions": repetitions,
        "cases": cases,
        "aggregate_status": "PASS" if statuses and all(status == "PASS" for status in statuses) else "FAIL",
        "delivery_guarantee": "at-least-once-compatible with explicit duplicate acknowledgement; exactly-once is not claimed",
    }


def write_outputs(payload: Dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "delivery-races.json", payload)
    lines = [
        "# v0.2 delivery crash and concurrency races",
        "",
        f"Generated: `{payload['generated_at']}`",
        f"Repetitions: `{payload['repetitions']}`",
        f"Aggregate: **{payload['aggregate_status']}**",
        "",
        "| Case | Runs | PASS | PARTIAL | UNKNOWN | FAIL |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    names = sorted({case["name"] for case in payload["cases"]})
    for name in names:
        cases = [case for case in payload["cases"] if case["name"] == name]
        counts = {status: sum(case["status"] == status for case in cases) for status in ("PASS", "PARTIAL", "UNKNOWN", "FAIL")}
        lines.append(
            f"| `{name}` | {len(cases)} | {counts['PASS']} | {counts['PARTIAL']} | {counts['UNKNOWN']} | {counts['FAIL']} |"
        )
    lines.extend(
        [
            "",
            "No real Codex thread was used; Codex message and turn counts are",
            "therefore `NOT_ATTEMPTED`. The durable state tests do not establish",
            "exactly-once delivery.",
            "",
        ]
    )
    (OUT / "delivery-races.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Probe durable delivery crash and claim races")
    parser.add_argument("--runs", type=int, default=1, help="repetitions of all local cases")
    args = parser.parse_args(argv)
    payload = build_payload(args.runs)
    write_outputs(payload)
    print(json.dumps({"aggregate_status": payload["aggregate_status"], "cases": len(payload["cases"])}))
    return 0 if payload["aggregate_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
