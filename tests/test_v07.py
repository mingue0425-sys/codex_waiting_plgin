from __future__ import annotations

import unittest

from snooze_controller.v07_provenance import (
    InvalidNativeTransition,
    NativeState,
    NativeStateMachine,
    ProcessIdKind,
    classify_process_id,
    correlate_probe_evidence,
)


class V07ProvenanceTests(unittest.TestCase):
    def _records(self) -> dict:
        return correlate_probe_evidence(
            expected_nonce="nonce-1",
            item_record={
                "item_id": "item-1",
                "process_id": "logical-1",
                "cwd": "/workspace",
                "command": "python3 probe.py --nonce nonce-1",
                "process_id_source": "thread/backgroundTerminals",
            },
            output_text='{"nonce":"nonce-1","pid":123,"cwd":"/workspace"}',
            marker_record={"nonce": "nonce-1"},
            self_report={"nonce": "nonce-1", "pid": 123, "cwd": "/workspace"},
            os_observation={
                "pid": 123,
                "cwd": "/workspace",
                "command_match": True,
                "start_identity_match": True,
            },
            expected_cwd="/workspace",
            expected_command="python3 probe.py --nonce nonce-1",
            logical_process_id="logical-1",
        )

    def test_independent_evidence_passes_without_pid_equality(self) -> None:
        value = self._records()
        self.assertEqual(value["status"], "PASS")
        self.assertEqual(value["process_id_kind"], ProcessIdKind.LOGICAL_HANDLE.value)
        self.assertEqual(value["evidence"]["ITEM_NONCE_MATCH"], "PASS")

    def test_missing_evidence_is_unknown(self) -> None:
        value = correlate_probe_evidence(
            expected_nonce="nonce-1",
            item_record={"process_id": "logical-1"},
            output_text=None,
            marker_record=None,
            self_report=None,
            os_observation=None,
            expected_cwd="/workspace",
            logical_process_id="logical-1",
        )
        self.assertEqual(value["status"], "UNKNOWN")

    def test_mismatch_fails(self) -> None:
        value = self._records()
        value = correlate_probe_evidence(
            expected_nonce="nonce-1",
            item_record={"cwd": "/workspace", "command": "python3 other.py"},
            output_text='{"nonce":"nonce-1","pid":123}',
            marker_record={"nonce": "nonce-1"},
            self_report={"nonce": "nonce-1", "pid": 123, "cwd": "/workspace"},
            os_observation={"pid": 123, "cwd": "/workspace"},
            expected_cwd="/workspace",
            expected_command="python3 probe.py",
            logical_process_id="logical-1",
        )
        self.assertEqual(value["status"], "FAIL")

    def test_process_kind_does_not_infer_os_pid_from_name(self) -> None:
        self.assertEqual(
            classify_process_id("42", self_reported_os_pid=123, observed_os_pid=123),
            ProcessIdKind.LOGICAL_HANDLE,
        )
        self.assertEqual(
            classify_process_id(123, self_reported_os_pid=123, observed_os_pid=123),
            ProcessIdKind.OS_PID,
        )
        self.assertEqual(classify_process_id("opaque"), ProcessIdKind.OPAQUE)

    def test_state_machine_rejects_close_before_provenance(self) -> None:
        machine = NativeStateMachine()
        machine.transition(NativeState.COMMAND_ITEM_STARTED)
        machine.transition(NativeState.PROVENANCE_PENDING)
        with self.assertRaises(InvalidNativeTransition):
            machine.transition(NativeState.TURN_CLOSED)

    def test_state_machine_accepts_native_sequence(self) -> None:
        machine = NativeStateMachine()
        for state in (
            NativeState.COMMAND_ITEM_STARTED,
            NativeState.PROVENANCE_PENDING,
            NativeState.PROVENANCE_ESTABLISHED,
            NativeState.FOREGROUND_RUNNING,
            NativeState.YIELDED,
            NativeState.HANDOFF_PENDING,
            NativeState.TURN_CLOSED,
            NativeState.BACKGROUND_RUNNING,
            NativeState.COMPLETING,
            NativeState.COMPLETED,
            NativeState.CONTINUATION_PENDING,
            NativeState.CONTINUED,
        ):
            machine.transition(state, provenance_established=True)
        self.assertEqual(machine.state, NativeState.CONTINUED)


if __name__ == "__main__":
    unittest.main()
