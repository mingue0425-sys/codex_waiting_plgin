from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from snooze_controller.handoff import DynamicHandoffTool, HandoffController, find_detached_marker
from snooze_controller.app_server_process import ServerRequest
from snooze_core.store import JobStore


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "codex-snooze"


class FakeProcess:
    def __init__(self) -> None:
        self.requests = []

    def request(self, method, params, *, timeout):
        self.requests.append((method, params, timeout))
        return {
            "id": 9,
            "result": {
                "exitCode": 0,
                "stdout": json.dumps(
                    {
                        "codex_snooze": True,
                        "state": "DETACHED",
                        "event_version": 1,
                        "job_id": "job-fake",
                        "threshold_seconds": 10,
                    }
                ),
                "stderr": "",
            },
        }


class V04Tests(unittest.TestCase):
    def test_marker_requires_structured_detached_object(self) -> None:
        self.assertIsNone(find_detached_marker("codex_snooze=true state=DETACHED job_id=job-fake"))
        self.assertIsNone(find_detached_marker({"state": "DETACHED", "job_id": "job-fake"}))
        marker = find_detached_marker(
            "prefix {\"codex_snooze\":true,\"state\":\"DETACHED\",\"job_id\":\"job-fake\"} suffix"
        )
        self.assertEqual(marker["job_id"], "job-fake")

    def test_dynamic_tool_uses_nested_command_exec_and_rejects_approval(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-v04-tool-") as temporary:
            root = Path(temporary)
            process = FakeProcess()
            tool = DynamicHandoffTool(JobStore(root / "store"), cwd=root, process=process)
            request = ServerRequest(
                request_id=3,
                method="item/tool/call",
                params={
                    "tool": tool.name,
                    "arguments": {
                        "command": ["python3", "-c", "print('ok')"],
                        "threshold_seconds": 10,
                        "cwd": str(root),
                    },
                },
                received_at="now",
            )
            response = tool(request)
            self.assertTrue(response["result"]["success"])
            self.assertEqual(process.requests[0][0], "command/exec")
            self.assertEqual(process.requests[0][1]["sandboxPolicy"]["type"], "workspaceWrite")
            self.assertEqual(process.requests[0][1]["sandboxPolicy"]["writableRoots"], [str(root.resolve())])
            approval = ServerRequest(4, "item/commandExecution/requestApproval", {}, "now")
            self.assertIsNone(tool(approval))

    def test_short_handoff_is_transparent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-v04-short-") as temporary:
            root = Path(temporary)
            command = [
                sys.executable,
                str(LAUNCHER),
                "--store",
                str(root / "store"),
                "handoff",
                "--threshold",
                "10",
                "--cwd",
                str(root),
                "--",
                sys.executable,
                "-c",
                "import sys; print('SHORT_STDOUT'); print('SHORT_STDERR', file=sys.stderr)",
            ]
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=20, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = json.loads(completed.stdout)
            self.assertEqual(output["state"], "COMPLETED")
            self.assertEqual(output["exit_code"], 0)
            store = JobStore(root / "store")
            result = store.read_result(output["job_id"])
            self.assertEqual(result["exit_code"], 0)
            self.assertIn("SHORT_STDOUT", result["log_preview"])
            self.assertIn("SHORT_STDERR", result["log_preview"])
            self.assertIsNone(store.read_handoff_marker(output["job_id"]))

    def test_detached_failure_preserves_exit_code(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-v04-failure-") as temporary:
            root = Path(temporary)
            command = [
                sys.executable,
                str(LAUNCHER),
                "--store",
                str(root / "store"),
                "handoff",
                "--threshold",
                "0.05",
                "--cwd",
                str(root),
                "--",
                sys.executable,
                "-c",
                "import time; time.sleep(.25); raise SystemExit(7)",
            ]
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=20, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            marker = json.loads(completed.stdout)
            self.assertEqual(marker["state"], "DETACHED")
            store = JobStore(root / "store")
            job_id = marker["job_id"]
            for _ in range(100):
                result = store.read_result(job_id)
                if result is not None:
                    break
                import time

                time.sleep(0.05)
            self.assertIsNotNone(result)
            self.assertEqual(result["execution_state"], "FAILED")
            self.assertEqual(result["exit_code"], 7)

    def test_model_activity_filter_excludes_controller_events(self) -> None:
        class Process:
            events = [
                {"method": "turn/interrupt", "monotonic_ns": 5},
                {"method": "thread/tokenUsage/updated", "monotonic_ns": 6},
                {"method": "turn/started", "monotonic_ns": 7},
                {"method": "item/agentMessage/delta", "monotonic_ns": 8},
            ]

        values = HandoffController.model_activity_between(Process(), 5, 8)
        self.assertEqual([item["method"] for item in values], ["turn/started", "item/agentMessage/delta"])


if __name__ == "__main__":
    unittest.main()
