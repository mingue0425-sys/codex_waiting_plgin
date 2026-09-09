from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

from snooze_controller.agent import AgentController
from snooze_controller.app_server_process import AppServerLifecycle, AppServerProcess, AppServerProcessError
from snooze_controller.completion_router import CompletionRouter, CompletionRouterError
from snooze_controller.native_backend import NativeAppServerBackend, NativeBackendError
from snooze_controller.thread_registry import ThreadRegistry


ROOT = Path(__file__).resolve().parents[1]
FAKE = ROOT / "probes" / "fixtures" / "fake_app_server.py"


class DummyController:
    thread_id = "thread-1"

    def __init__(self) -> None:
        self.turns = []

    def start_turn(self, prompt: str, *, timeout: float = 30.0):
        turn_id = f"turn-{len(self.turns) + 1}"
        self.turns.append((turn_id, prompt))
        return {"id": turn_id}

    def wait_turn(self, turn_id: str, *, timeout: float = 300.0):
        return {"id": turn_id, "status": "completed"}


class V03Tests(unittest.TestCase):
    def fake(self, *flags: str) -> AppServerProcess:
        return AppServerProcess([sys.executable, str(FAKE), *flags], cwd=ROOT)

    def test_partial_jsonl_and_owned_thread_turn(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v03-test-") as temporary:
            registry = ThreadRegistry(Path(temporary) / "threads.json")
            process = self.fake()
            try:
                process.start(timeout=5)
                controller = AgentController(registry, process=process)
                controller.create_thread(cwd=ROOT, sandbox="workspace-write", approval_policy="on-request", timeout=5)
                turn = controller.start_turn("fake task", timeout=5)
                completed = controller.wait_turn(turn["id"], timeout=5)
                self.assertEqual(completed["status"], "completed")
                self.assertEqual(controller.thread_id, "fake-thread")
                self.assertEqual(registry.get("fake-thread")["owner"], "codex-snooze")
                self.assertEqual(process.state, AppServerLifecycle.READY)
                self.assertTrue(any(event.get("kind") == "response" for event in process.events))
            finally:
                process.stop()

    def test_default_server_request_is_rejected(self) -> None:
        process = self.fake("--server-request")
        try:
            process.start(timeout=5)
            process.request("thread/start", {"cwd": str(ROOT)}, timeout=5)
            turn = process.request("turn/start", {"threadId": "fake-thread", "input": []}, timeout=5)
            self.assertEqual(turn.get("error"), None)
            self.assertEqual(process.server_requests[0].method, "item/commandExecution/requestApproval")
            responses = [event for event in process.events if event.get("kind") == "server_response"]
            self.assertTrue(any((event.get("error") or {}).get("code") == -32001 for event in responses))
        finally:
            process.stop()

    def test_response_timeout_does_not_fake_completion(self) -> None:
        process = self.fake("--drop-turn-response")
        try:
            process.start(timeout=5)
            process.request("thread/start", {"cwd": str(ROOT)}, timeout=5)
            with self.assertRaises(AppServerProcessError):
                process.request("turn/start", {"threadId": "fake-thread", "input": []}, timeout=0.1)
        finally:
            process.stop()

    def test_registry_and_router_are_durable_and_duplicate_aware(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v03-router-") as temporary:
            root = Path(temporary)
            registry = ThreadRegistry(root / "threads.json")
            record = registry.register("thread-1", app_server_instance="instance-1", cwd=ROOT)
            self.assertEqual(record["owner"], "codex-snooze")
            router = CompletionRouter(root / "events.json")
            dummy = DummyController()
            routed = router.route(dummy, event_id="event-1", job_id="job-1", result_sha256="hash", prompt="continue")
            self.assertEqual(routed["state"], "SENT_UNCONFIRMED")
            with self.assertRaises(CompletionRouterError):
                router.route(dummy, event_id="event-1", job_id="job-1", result_sha256="hash", prompt="duplicate")
            self.assertEqual(router.acknowledge("event-1")["state"], "ACKED")

    def test_native_backend_requires_experimental_capability(self) -> None:
        process = self.fake()
        with self.assertRaises(NativeBackendError):
            NativeAppServerBackend(process)

    def test_crash_state_is_separate_from_thread_state(self) -> None:
        process = self.fake("--crash-after-initialize")
        process.start(timeout=5)
        try:
            time.sleep(0.2)
            self.assertIn(process.state, {AppServerLifecycle.CRASHED, AppServerLifecycle.FAILED})
        finally:
            process.stop()


if __name__ == "__main__":
    unittest.main()
