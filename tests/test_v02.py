from __future__ import annotations

import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from probes.v0_2_app_server import METHODS
from probes.v0_2_report import build_payload, mixed_status
from snooze_controller.gate import choose_control_plane
from snooze_plugin.hooks import pre_tool_use
from tools.app_server_probe import AppServerClient, extract_ids, redact


class V02Tests(unittest.TestCase):
    def test_redaction_bounds_credentials_and_text(self) -> None:
        value = redact(
            {
                "api_key": "do-not-record",
                "nested": {"password": "also-secret"},
                "message": "prefix " + "sk_" + "12345678901234567890 suffix",
                "preview": "private thread content",
                "path": "/Users/example/.codex/sessions/private.jsonl",
                "long": "x" * 20,
            },
            max_string=100,
        )
        self.assertEqual(value["api_key"], "<redacted>")
        self.assertEqual(value["nested"]["password"], "<redacted>")
        self.assertIn("<redacted>", value["message"])
        self.assertEqual(value["preview"], "<redacted-runtime-content>")
        self.assertEqual(value["path"], "<redacted-runtime-content>")
        bounded = redact({"long": "x" * 20}, max_string=8)
        self.assertTrue(bounded["long"].endswith("…<truncated>"))

    def test_extract_ids_accepts_protocol_casing(self) -> None:
        self.assertEqual(extract_ids({"threadId": "t", "turnId": "u"}), ("t", "u"))
        self.assertEqual(extract_ids({"thread_id": "t", "expectedTurnId": "u"}), ("t", "u"))
        self.assertEqual(extract_ids(None), (None, None))

    def test_app_server_client_preserves_notifications_and_records_trace(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-v02-client-") as temporary:
            server = Path(temporary) / "fake_server.py"
            server.write_text(
                textwrap.dedent(
                    """
                    import json
                    import sys
                    for line in sys.stdin:
                        message = json.loads(line)
                        if message.get("method") == "initialize":
                            print(json.dumps({"method": "thread/status/changed", "params": {"threadId": "t", "status": {"type": "idle"}}}), flush=True)
                            print(json.dumps({"jsonrpc": "2.0", "id": 99, "result": {"apiKey": "test-" + "key-value"}}), flush=True)
                            print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"ok": True}}), flush=True)
                        elif message.get("method") == "ping":
                            print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": {"pong": True}}), flush=True)
                    """
                ),
                encoding="utf-8",
            )
            with AppServerClient([sys.executable, str(server)], cwd=Path(temporary)) as client:
                initialize = client.request("initialize", {"clientInfo": {"name": "test", "version": "1"}})
                self.assertEqual(initialize["result"], {"ok": True})
                ping = client.request("ping", {})
                self.assertEqual(ping["result"], {"pong": True})
                self.assertEqual(client.notifications[0]["method"], "thread/status/changed")
                snapshot = client.snapshot()
                serialized = json.dumps(snapshot, ensure_ascii=False)
                self.assertNotIn("test-key-value", serialized)
                self.assertIn("response", serialized)

    def test_schema_inventory_names_cover_required_control_plane_methods(self) -> None:
        for name in (
            "thread/start",
            "thread/resume",
            "thread/read",
            "thread/list",
            "thread/status (notification)",
            "turn/start",
            "turn/interrupt",
            "turn/steer",
            "turn/start.toolOutput",
            "thread/backgroundTerminals/list",
        ):
            self.assertIn(name, METHODS)

    def test_status_aggregation_is_conservative(self) -> None:
        self.assertEqual(mixed_status(["PASS"]), "PASS")
        self.assertEqual(mixed_status(["PASS", "UNKNOWN"]), "PARTIAL")
        self.assertEqual(mixed_status(["PASS", "FAIL"]), "FAIL")
        self.assertEqual(mixed_status(["UNKNOWN"]), "UNKNOWN")

    def test_control_plane_requires_all_direct_gates(self) -> None:
        direct = {
            "app_server_control": "PASS",
            "desktop_ui_integration": "PASS",
            "sandbox_preserved": "PASS",
            "approval_preserved": "PASS",
            "turn_interrupt": "PASS",
            "busy_thread_delivery": "PASS",
            "job_survives_interrupt": "PASS",
            "automatic_completion_delivery": "PASS",
        }
        self.assertEqual(choose_control_plane(direct), "APP_SERVER_DIRECT")
        direct["busy_thread_delivery"] = "UNKNOWN"
        self.assertEqual(choose_control_plane(direct), "APP_SERVER_PARTIAL")

    def test_pre_tool_hook_is_still_transparent(self) -> None:
        decision = pre_tool_use({"command": "printf SAFE", "cwd": str(ROOT)})
        self.assertEqual(decision["action"], "PASS_THROUGH")
        self.assertEqual(decision["feature_gate"], "AUTOMATIC_INTERCEPTION_DISABLED")

    def test_report_uses_app_server_partial_when_safety_is_unproven(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-v02-report-") as temporary:
            base = Path(temporary)
            fixtures = {
                "codex-environment.json": {"status": "PASS", "codex_version": "test"},
                "app-server-probes.json": {
                    "capabilities": {
                        "app_server_initialize": "PASS",
                        "thread_list": "PASS",
                        "thread_start_read_background_queue_list": "PASS",
                    }
                },
                "interrupt-tests.json": {"aggregate_status": "UNKNOWN"},
                "delivery-races.json": {"aggregate_status": "PASS"},
                "concurrency-tests.json": {
                    "capabilities": {
                        "busy_thread_delivery": "PARTIAL",
                        "idle_check_to_external_turn_race": "UNKNOWN",
                        "automatic_completion_delivery": "UNKNOWN",
                    }
                },
                "security-probes.json": {
                    "capabilities": {
                        "actual_command_integrity": "PASS",
                        "environment_value_persistence": "PASS",
                        "sandbox_preserved": "UNKNOWN",
                        "approval_preserved": "UNKNOWN",
                        "network_boundary": "UNKNOWN",
                        "auto_pretool_rewrite": "FAIL",
                        "auto_pretool_interception": "FAIL",
                    }
                },
                "project-stale-tests.json": {"aggregate_status": "PASS"},
            }
            for name, value in fixtures.items():
                (base / name).write_text(json.dumps(value), encoding="utf-8")
            payload = build_payload(base)
            self.assertEqual(payload["control_plane"], "APP_SERVER_PARTIAL")
            self.assertFalse(payload["automatic_features"]["auto_handoff"])
            self.assertEqual(payload["feature_flags"]["auto_pretool_interception"], "FAIL")
            self.assertEqual(payload["integration_flags"]["AUTO_PRETOOL_INTERCEPTION_SUPPORTED"], "FAIL")


if __name__ == "__main__":
    unittest.main()
