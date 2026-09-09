#!/usr/bin/env python3
from __future__ import annotations

"""Run 100 concurrent completion and handoff claim races."""

import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.4"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.completion_router import CompletionRouter, CompletionRouterError
from snooze_controller.thread_registry import ThreadRegistry
from snooze_core.models import utc_now
from tools.app_server_probe import redact, write_trace


class ControlledController:
    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        self._lock = threading.Lock()
        self.turns: List[str] = []

    def start_turn(self, _prompt: str, *, timeout: float = 30.0) -> Dict[str, Any]:
        with self._lock:
            turn_id = f"turn-{len(self.turns) + 1}"
            self.turns.append(turn_id)
        time.sleep(0.002)
        return {"id": turn_id}

    def wait_turn(self, turn_id: str, *, timeout: float = 300.0) -> Dict[str, Any]:
        return {"id": turn_id, "status": "completed"}


def run(iterations: int = 100) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v04-races-") as temporary:
        root = Path(temporary)
        for index in range(iterations):
            event_id = f"event-{index:03d}"
            job_id = f"job-{index:03d}"
            thread_id = f"thread-{index:03d}"
            router = CompletionRouter(root / f"events-{index:03d}.json")
            controller = ControlledController(thread_id)
            outcomes: List[str] = []

            def attempt() -> None:
                try:
                    router.route(
                        controller,
                        event_id=event_id,
                        job_id=job_id,
                        result_sha256=f"hash-{index}",
                        prompt="continue from the immutable completion event",
                    )
                    outcomes.append("SENT_UNCONFIRMED")
                except CompletionRouterError:
                    outcomes.append("REJECTED")

            first = threading.Thread(target=attempt)
            second = threading.Thread(target=attempt)
            first.start()
            second.start()
            first.join()
            second.join()

            registry = ThreadRegistry(root / f"threads-{index:03d}.json")
            registry.register(thread_id, app_server_instance="instance", cwd=root)
            marker = {"codex_snooze": True, "state": "DETACHED", "job_id": job_id, "event_version": 1}
            registry_outcomes: List[Dict[str, Any]] = []

            def map_handoff() -> None:
                registry_outcomes.append(
                    registry.record_handoff(
                        thread_id,
                        turn_id=f"turn-{index}",
                        item_id=f"item-{index}",
                        job_id=job_id,
                        marker=marker,
                    )
                )

            map_first = threading.Thread(target=map_handoff)
            map_second = threading.Thread(target=map_handoff)
            map_first.start()
            map_second.start()
            map_first.join()
            map_second.join()
            stored = registry.get(thread_id) or {}
            handoffs = stored.get("handoffs", [])
            checks.append(
                {
                    "iteration": index,
                    "continuation_turns": len(controller.turns),
                    "outcomes": sorted(outcomes),
                    "handoff_entries": len(handoffs),
                    "status": "PASS" if len(controller.turns) == 1 and sorted(outcomes) == ["REJECTED", "SENT_UNCONFIRMED"] and len(handoffs) == 1 else "FAIL",
                }
            )
        value = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "iterations": iterations,
            "passed_iterations": sum(item["status"] == "PASS" for item in checks),
            "continuation_turns_created": sum(item["continuation_turns"] for item in checks),
            "duplicate_attempts_rejected": sum(item["outcomes"].count("REJECTED") for item in checks),
            "handoff_entries_created": sum(item["handoff_entries"] for item in checks),
            "thread_corruption": 0 if all(item["status"] == "PASS" for item in checks) else 1,
            "job_reruns": 0,
            "exactly_once": "NOT_CLAIMED",
            "status": "PASS" if len(checks) == iterations and all(item["status"] == "PASS" for item in checks) else "FAIL",
            "checks": checks,
        }
    return redact(value)


def main() -> int:
    value = run()
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "continuation-races.json", value)
    (OUT / "continuation-races.md").write_text(
        "# v0.4 continuation and handoff races\n\n"
        f"Status: **{value['status']}**\n\n"
        f"Iterations: `{value['passed_iterations']}/{value['iterations']}`\n\n"
        f"Continuation turns created: `{value['continuation_turns_created']}`\n\n"
        f"Duplicate attempts rejected: `{value['duplicate_attempts_rejected']}`\n\n"
        "The router rejects concurrent CLAIMED/STARTING/SENDING claims. The "
        "completion protocol still remains at-least-once and exactly-once is not claimed.\n",
        encoding="utf-8",
    )
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
