#!/usr/bin/env python3
from __future__ import annotations

"""Short ownership-transfer race suite for the v0.5 state model."""

import json
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.5"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.v05_backend import (
    OwnershipLedger,
    OwnershipState,
    PendingHandoff,
    V05Backend,
)
from snooze_core.models import utc_now
from tools.app_server_probe import redact, write_trace


def run(iterations: int = 100) -> Dict[str, Any]:
    successes = 0
    expected_cas_rejections = 0
    unexpected_errors: List[str] = []
    dual_owner = 0
    terminal_completed = 0
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v05-races-") as temporary:
        ledger = OwnershipLedger(Path(temporary) / "ownership.json")
        for index in range(iterations):
            job_id = f"race-{index}"
            pending = PendingHandoff(
                handoff_id=f"handoff-{index}",
                job_id=job_id,
                thread_id=f"thread-{index}",
                turn_id=f"turn-{index}",
                expected_command_sha256=f"hash-{index}",
                threshold_seconds=10.0,
                backend=V05Backend.THREAD_NATIVE_TERMINAL,
            )
            ledger.create(pending)
            ledger.transition(job_id, expected=OwnershipState.FOREGROUND, target=OwnershipState.HANDOFF_PENDING)
            results: List[str] = []
            lock = threading.Lock()

            def contender() -> None:
                try:
                    ledger.transition(
                        job_id,
                        expected=OwnershipState.HANDOFF_PENDING,
                        target=OwnershipState.NATIVE_BACKGROUND,
                        backend=V05Backend.THREAD_NATIVE_TERMINAL,
                    )
                    with lock:
                        results.append("won")
                except ValueError as exc:
                    if "compare-and-swap" in str(exc):
                        with lock:
                            results.append("cas_rejected")
                    else:
                        with lock:
                            unexpected_errors.append(str(exc))
                except Exception as exc:  # pragma: no cover - evidence guard
                    with lock:
                        unexpected_errors.append(f"{type(exc).__name__}: {exc}")

            first = threading.Thread(target=contender)
            second = threading.Thread(target=contender)
            first.start()
            second.start()
            first.join(timeout=5)
            second.join(timeout=5)
            if results.count("won") == 1 and results.count("cas_rejected") == 1:
                successes += 1
                expected_cas_rejections += 1
            else:
                dual_owner += 1 if results.count("won") > 1 else 0
            record = ledger.get(job_id) or {}
            if record.get("state") == OwnershipState.NATIVE_BACKGROUND.value:
                ledger.transition(job_id, expected=OwnershipState.NATIVE_BACKGROUND, target=OwnershipState.COMPLETING)
                ledger.transition(job_id, expected=OwnershipState.COMPLETING, target=OwnershipState.COMPLETED)
                terminal_completed += 1
            else:
                unexpected_errors.append(f"{job_id}: unexpected final state {record.get('state')}")
        value = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "iterations": iterations,
            "ownership_claims": successes,
            "expected_compare_and_swap_rejections": expected_cas_rejections,
            "dual_owner_observations": dual_owner,
            "terminal_completed": terminal_completed,
            "unexpected_errors": unexpected_errors,
            "status": "PASS" if successes == iterations and terminal_completed == iterations and dual_owner == 0 and not unexpected_errors else "FAIL",
            "exactly_once": "NOT_CLAIMED",
            "scope": "durable ownership ledger only; no claim about Codex tool execution or delivery exactly-once",
        }
        return redact(value)


def main() -> int:
    value = run()
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "handoff-races.json", value)
    (OUT / "handoff-races.md").write_text(
        "# v0.5 ownership handoff races\n\n"
        f"Status: **{value['status']}**\n\n"
        f"Iterations: `{value['iterations']}`\n\n"
        f"Single winner claims: `{value['ownership_claims']}`\n\n"
        f"CAS rejections: `{value['expected_compare_and_swap_rejections']}`\n\n"
        "The suite validates the local ownership state machine. It does not claim exactly-once completion delivery.\n",
        encoding="utf-8",
    )
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
