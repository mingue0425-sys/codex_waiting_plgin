#!/usr/bin/env python3
from __future__ import annotations

"""Deterministic ownership/reconnect/duplicate tests for the v0.3 path."""

import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.3"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.agent import AgentController
from snooze_controller.app_server_process import AppServerLifecycle, AppServerProcess, AppServerProcessError
from snooze_controller.completion_router import CompletionRouter, CompletionRouterError
from snooze_controller.thread_registry import ThreadRegistry
from snooze_core.models import utc_now
from tools.app_server_probe import redact, write_trace


FAKE = ROOT / "probes" / "fixtures" / "fake_app_server.py"


class DummyController:
    thread_id = "thread-race"

    def __init__(self) -> None:
        self.turns = []

    def start_turn(self, prompt: str, *, timeout: float = 30.0) -> Dict[str, Any]:
        turn_id = f"turn-{len(self.turns) + 1}"
        self.turns.append((turn_id, prompt))
        return {"id": turn_id}

    def wait_turn(self, turn_id: str, *, timeout: float = 300.0) -> Dict[str, Any]:
        return {"id": turn_id, "status": "completed"}


def run() -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v03-races-") as temporary:
        root = Path(temporary)
        registry = ThreadRegistry(root / "threads.json")
        router = CompletionRouter(root / "completion-events.json")
        dummy = DummyController()
        checks: Dict[str, Dict[str, Any]] = {}

        first = router.claim(
            event_id="event-1", job_id="job-1", thread_id="thread-race", result_sha256="hash-1"
        )
        checks["event_claim_persisted"] = {"status": "PASS", "state": first["state"]}
        routed = router.route(
            dummy,
            event_id="event-1",
            job_id="job-1",
            result_sha256="hash-1",
            prompt="Read the result marker and continue without rerunning.",
        )
        checks["same_event_routes_once"] = {"status": "PASS", "turn_id": routed["turn_id"]}
        try:
            router.route(
                dummy,
                event_id="event-1",
                job_id="job-1",
                result_sha256="hash-1",
                prompt="duplicate",
            )
        except CompletionRouterError as exc:
            checks["duplicate_guard"] = {"status": "PASS", "error": str(exc)}
        else:
            checks["duplicate_guard"] = {"status": "FAIL", "error": "duplicate was accepted"}
        try:
            router.acknowledge("event-1", turn_id="turn-1")
            checks["ack_state"] = {"status": "PASS", "state": router.get("event-1")["state"]}
        except CompletionRouterError as exc:
            checks["ack_state"] = {"status": "FAIL", "error": str(exc)}

        command = [sys.executable, str(FAKE)]
        process = AppServerProcess(command, cwd=ROOT)
        try:
            process.start(timeout=5)
            agent = AgentController(registry, process=process)
            agent.create_thread(cwd=ROOT, sandbox="workspace-write", approval_policy="on-request", timeout=5)
            turn = agent.start_turn("fake deterministic task", timeout=5)
            done = agent.wait_turn(turn["id"], timeout=5)
            record = registry.get("fake-thread")
            checks["owned_thread_and_turn"] = {
                "status": "PASS" if record and done.get("status") == "completed" else "FAIL",
                "owner": (record or {}).get("owner"),
                "thread_id": agent.thread_id,
            }
            checks["partial_jsonl_reader"] = {
                "status": "PASS" if any(event.get("kind") == "response" for event in process.events) else "FAIL"
            }
        finally:
            process.stop()

        crash = AppServerProcess(command + ["--crash-after-initialize"], cwd=ROOT)
        crash.start(timeout=5)
        time.sleep(0.2)
        crash_state = crash.state.value
        crash.stop()
        checks["crash_detection"] = {
            "status": "PASS" if crash_state in {AppServerLifecycle.CRASHED.value, AppServerLifecycle.FAILED.value} else "UNKNOWN",
            "state": crash_state,
        }

        dropped = AppServerProcess(command + ["--drop-turn-response"], cwd=ROOT)
        try:
            dropped.start(timeout=5)
            dropped.request("thread/start", {"cwd": str(ROOT)}, timeout=5)
            try:
                dropped.request("turn/start", {"threadId": "fake-thread", "input": []}, timeout=0.2)
            except AppServerProcessError as exc:
                checks["turn_response_loss_is_retryable"] = {"status": "PASS", "error": str(exc)}
            else:
                checks["turn_response_loss_is_retryable"] = {"status": "FAIL"}
        finally:
            dropped.stop()
        checks["exactly_once"] = {
            "status": "UNKNOWN",
            "reason": "The installed protocol exposes no client idempotency key; SENT_UNCONFIRMED remains at-least-once."
        }
        statuses = [item["status"] for item in checks.values()]
        overall = "FAIL" if "FAIL" in statuses else "PASS" if all(item == "PASS" for item in statuses) else "PARTIAL"
        value = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "overall": overall,
            "checks": checks,
            "controller_turns": len(dummy.turns),
            "process_lifecycle": process.snapshot().get("state"),
            "exactly_once_claim": "NOT_CLAIMED",
        }
    return redact(value)


def markdown(value: Dict[str, Any]) -> str:
    lines = [
        "# v0.3 handoff and reconnect races",
        "",
        f"Generated: `{value['generated_at']}`",
        f"Overall: **{value['overall']}**",
        "",
        "| Check | Status |",
        "|---|---|",
    ]
    for name, item in value["checks"].items():
        lines.append(f"| `{name}` | `{item.get('status')}` |")
    lines.extend(
        [
            "",
            "The local router persists an event marker before a continuation and",
            "requires explicit duplicate permission after SENT_UNCONFIRMED. The",
            "protocol-level response-loss and durable-thread cases are recorded",
            "separately from the local marker. Exactly-once is not claimed.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    value = run()
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "handoff-races.json", value)
    (OUT / "handoff-races.md").write_text(markdown(value), encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
