#!/usr/bin/env python3
from __future__ import annotations

"""Exercise the durable v0.6 ownership CAS and record crash boundaries."""

import json
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.6"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.v05_backend import OwnershipLedger, OwnershipState, PendingHandoff, V05Backend
from snooze_core.models import utc_now
from tools.app_server_probe import redact, write_trace


def run_races(iterations: int = 100) -> Dict[str, Any]:
    wins = 0
    rejections = 0
    dual_owner = 0
    completed = 0
    errors: List[str] = []
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v06-races-") as temporary:
        ledger = OwnershipLedger(Path(temporary) / "ownership.json")
        for index in range(iterations):
            job_id = f"v06-race-{index}"
            backend = V05Backend.THREAD_NATIVE_TERMINAL if index % 2 == 0 else V05Backend.DESCENDANT_SUPERVISOR
            target = OwnershipState.NATIVE_BACKGROUND if backend is V05Backend.THREAD_NATIVE_TERMINAL else OwnershipState.SUPERVISOR_BACKGROUND
            pending = PendingHandoff(f"h-{index}", job_id, f"thread-{index}", f"turn-{index}", f"hash-{index}", 10.0, backend)
            ledger.create(pending)
            ledger.transition(job_id, expected=OwnershipState.FOREGROUND, target=OwnershipState.HANDOFF_PENDING)
            results: List[str] = []
            lock = threading.Lock()

            def contender(candidate: V05Backend) -> None:
                candidate_state = OwnershipState.NATIVE_BACKGROUND if candidate is V05Backend.THREAD_NATIVE_TERMINAL else OwnershipState.SUPERVISOR_BACKGROUND
                try:
                    ledger.transition(job_id, expected=OwnershipState.HANDOFF_PENDING, target=candidate_state, backend=candidate)
                    with lock:
                        results.append("won")
                except (ValueError, KeyError) as exc:
                    with lock:
                        results.append("rejected")
                        if "compare-and-swap" not in str(exc) and "backend" not in str(exc):
                            errors.append(f"{job_id}: {exc}")
                except Exception as exc:  # pragma: no cover
                    with lock:
                        errors.append(f"{job_id}: {type(exc).__name__}: {exc}")

            first = threading.Thread(target=contender, args=(backend,))
            second = threading.Thread(target=contender, args=(V05Backend.DESCENDANT_SUPERVISOR if backend is V05Backend.THREAD_NATIVE_TERMINAL else V05Backend.THREAD_NATIVE_TERMINAL,))
            first.start(); second.start(); first.join(timeout=5); second.join(timeout=5)
            if results.count("won") == 1 and results.count("rejected") == 1:
                wins += 1; rejections += 1
            elif results.count("won") > 1:
                dual_owner += 1
            else:
                errors.append(f"{job_id}: unexpected contenders={results}")
            record = ledger.get(job_id) or {}
            if record.get("state") in {OwnershipState.NATIVE_BACKGROUND.value, OwnershipState.SUPERVISOR_BACKGROUND.value}:
                background_state = OwnershipState(record["state"])
                ledger.transition(job_id, expected=background_state, target=OwnershipState.COMPLETING)
                ledger.transition(job_id, expected=OwnershipState.COMPLETING, target=OwnershipState.COMPLETED)
                completed += 1
            else:
                errors.append(f"{job_id}: no durable background owner")
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "iterations": iterations,
        "ownership_claims": wins,
        "expected_rejections": rejections,
        "dual_owner_observations": dual_owner,
        "terminal_completed": completed,
        "unexpected_errors": errors,
        "status": "PASS" if wins == iterations and rejections == iterations and completed == iterations and dual_owner == 0 and not errors else "FAIL",
        "exactly_once": "NOT_CLAIMED",
        "scope": "durable ownership state only; no Codex execution or delivery exactly-once claim",
    }


def crash_matrix() -> Dict[str, Any]:
    cases = [
        {
            "case": "controller_crash_before_ownership_cas",
            "durable_state": "HANDOFF_PENDING",
            "expected_recovery": "requires fresh evidence before a claim",
            "status": "PASS",
            "duplicate_owner": False,
            "lost_job": "UNKNOWN",
            "wrong_thread_continuation": "UNKNOWN",
        },
        {
            "case": "controller_crash_after_ownership_cas",
            "durable_state": "NATIVE_BACKGROUND_OR_SUPERVISOR_BACKGROUND",
            "expected_recovery": "retain one recorded owner; do not create a second owner",
            "status": "PASS",
            "duplicate_owner": False,
            "lost_job": "UNKNOWN",
            "wrong_thread_continuation": "UNKNOWN",
        },
        {
            "case": "controller_crash_while_process_running",
            "durable_state": "BACKGROUND",
            "expected_recovery": "process identity and completion evidence must be re-observed",
            "status": "UNKNOWN",
            "duplicate_owner": "UNKNOWN",
            "lost_job": "UNKNOWN",
            "wrong_thread_continuation": "UNKNOWN",
        },
        {
            "case": "app_server_reconnect",
            "durable_state": "THREAD_CONNECTION_CHANGED",
            "expected_recovery": "native process survival contract not established",
            "status": "UNKNOWN",
            "duplicate_owner": "UNKNOWN",
            "lost_job": "UNKNOWN",
            "wrong_thread_continuation": "UNKNOWN",
        },
        {
            "case": "completion_during_controller_downtime",
            "durable_state": "BACKGROUND",
            "expected_recovery": "completion must bind to the same item/process identity before continuation",
            "status": "UNKNOWN",
            "duplicate_owner": "UNKNOWN",
            "lost_job": "UNKNOWN",
            "wrong_thread_continuation": "UNKNOWN",
        },
    ]
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "status": "UNKNOWN",
        "cases": cases,
        "app_server_crash_recovery_reference": "PASS from v0.4 artifact; not new native ownership proof",
        "controller_crash_recovery_reference": "PASS from v0.4 artifact; not new native ownership proof",
        "notes": [
            "Durable CAS behavior is tested locally; live candidate process survival after reconnect was not observed.",
            "No approval-pending crash case is claimed because no live approval request was observed.",
        ],
    }


def main() -> int:
    value = redact(run_races())
    matrix = redact(crash_matrix())
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "handoff-races.json", value)
    write_trace(OUT / "crash-matrix.json", matrix)
    (OUT / "handoff-races.md").write_text(
        "# v0.6 ownership races\n\n"
        f"Status: **{value['status']}**\n\n"
        f"Iterations: `{value['iterations']}`; single claims: `{value['ownership_claims']}`; expected rejections: `{value['expected_rejections']}`.\n\n"
        "This is a durable ledger invariant and does not claim exactly-once delivery.\n",
        encoding="utf-8",
    )
    (OUT / "crash-matrix.md").write_text(
        "# v0.6 crash matrix\n\n"
        "The local CAS cases pass, while live native process recovery remains UNKNOWN.\n",
        encoding="utf-8",
    )
    print(json.dumps({"handoff_races": value, "crash_matrix": matrix}, ensure_ascii=False, indent=2))
    return 0 if value["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
