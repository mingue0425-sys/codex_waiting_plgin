from __future__ import annotations

import unittest

from snooze_controller.v05_backend import V05Backend
from snooze_controller.v06_ownership import (
    classify_command_wrapper,
    command_integrity_v06,
    correlate_process_records,
    gate_ready,
    select_v06_backend,
)


def pass_evidence() -> dict:
    return {
        "status": "PASS",
        "sandbox_parity": "PASS",
        "approval_parity": "PASS",
        "command_integrity": "PASS",
        "process_identity": "PASS",
        "handoff_10s": "PASS",
        "job_survival": "PASS",
        "model_idle": "PASS",
        "completion_detection": "PASS",
        "auto_continuation": "PASS",
    }


class V06OwnershipTests(unittest.TestCase):
    def test_exact_process_correlation_requires_process_identity(self) -> None:
        event = {"item_id": "item-1", "process_id": "proc-1", "cwd": "workspace", "command": "python3 fixture.py", "os_pid": 123}
        background = {"itemId": "item-1", "processId": "proc-1", "cwd": "workspace", "command": "python3 fixture.py", "osPid": 123}
        value = correlate_process_records(event, background)
        self.assertEqual(value.status, "PASS")

    def test_process_correlation_without_identity_is_unknown(self) -> None:
        event = {"item_id": "item-1", "process_id": "proc-1", "cwd": "workspace", "command": "python3 fixture.py"}
        background = {"itemId": "item-1", "processId": "proc-1", "cwd": "workspace", "command": "python3 fixture.py"}
        self.assertEqual(correlate_process_records(event, background).status, "UNKNOWN")

    def test_process_correlation_mismatch_fails(self) -> None:
        event = {"item_id": "item-1", "process_id": "proc-1", "cwd": "workspace", "command": "python3 fixture.py", "os_pid": 123}
        background = {"itemId": "item-2", "processId": "proc-1", "cwd": "workspace", "command": "python3 fixture.py", "osPid": 123}
        self.assertEqual(correlate_process_records(event, background).status, "FAIL")

    def test_runtime_wrapper_is_recorded_without_being_approved(self) -> None:
        self.assertEqual(
            classify_command_wrapper("/bin/zsh -lc 'python3 fixture.py'", source="unifiedExecStartup"),
            "NORMAL_CODEX_RUNTIME_WRAPPER",
        )
        value = command_integrity_v06(
            "python3 fixture.py",
            event_command="/bin/zsh -lc 'python3 fixture.py'",
            background_command=None,
            requested_cwd="workspace",
            event_cwd="workspace",
            event_source="unifiedExecStartup",
        )
        self.assertEqual(value["status"], "UNKNOWN")
        self.assertFalse(value["approval_semantics_proven"])

    def test_command_integrity_requires_all_stages(self) -> None:
        value = command_integrity_v06(
            "python3 fixture.py",
            event_command="python3 fixture.py",
            background_command="python3 fixture.py",
            executed_command="python3 fixture.py",
            requested_cwd="workspace",
            event_cwd="workspace",
            background_cwd="workspace",
            executed_cwd="workspace",
        )
        self.assertEqual(value["status"], "PASS")
        mutated = command_integrity_v06(
            "python3 fixture.py",
            event_command="python3 fixture.py",
            background_command="python3 other.py",
            requested_cwd="workspace",
            event_cwd="workspace",
            background_cwd="workspace",
        )
        self.assertEqual(mutated["status"], "FAIL")

    def test_selector_requires_every_v06_gate(self) -> None:
        self.assertTrue(gate_ready(pass_evidence()))
        self.assertEqual(select_v06_backend({"THREAD_NATIVE_TERMINAL": pass_evidence()}), V05Backend.THREAD_NATIVE_TERMINAL)
        partial = pass_evidence()
        partial["process_identity"] = "UNKNOWN"
        self.assertEqual(select_v06_backend({"THREAD_NATIVE_TERMINAL": partial}), V05Backend.CLI_RESUME_FALLBACK)

    def test_selector_never_selects_controller_backend(self) -> None:
        self.assertEqual(select_v06_backend({"CONTROLLER_COMMAND_EXEC": pass_evidence()}), V05Backend.CLI_RESUME_FALLBACK)


if __name__ == "__main__":
    unittest.main()
