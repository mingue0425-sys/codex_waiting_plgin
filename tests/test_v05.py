from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from snooze_controller.v05_backend import (
    OwnershipLedger,
    OwnershipState,
    PendingHandoff,
    V05Backend,
    command_integrity,
    command_sha256,
    select_backend,
)


def pass_evidence() -> dict:
    return {
        "status": "PASS",
        "sandbox_parity": "PASS",
        "approval_parity": "PASS",
        "handoff_10s": "PASS",
        "job_survival": "PASS",
        "model_idle": "PASS",
        "auto_continuation": "PASS",
        "command_integrity": "PASS",
    }


class V05Tests(unittest.TestCase):
    def test_command_hash_is_exact_and_stable(self) -> None:
        self.assertEqual(command_sha256("python3 fixture.py"), command_sha256("python3 fixture.py"))
        self.assertNotEqual(command_sha256("python3 fixture.py"), command_sha256("python3  fixture.py"))

    def test_command_integrity_does_not_claim_approval(self) -> None:
        value = command_integrity("python3 fixture.py", "python3 fixture.py", "python3 fixture.py")
        self.assertEqual(value["status"], "PASS")
        self.assertFalse(value["approval_semantics_proven"])

    def test_selector_requires_every_native_gate(self) -> None:
        self.assertEqual(select_backend({"THREAD_NATIVE_TERMINAL": pass_evidence()}), V05Backend.THREAD_NATIVE_TERMINAL)
        partial = pass_evidence()
        partial["approval_parity"] = "UNKNOWN"
        self.assertEqual(select_backend({"THREAD_NATIVE_TERMINAL": partial}), V05Backend.CLI_RESUME_FALLBACK)

    def test_selector_can_choose_descendant_only_after_full_evidence(self) -> None:
        self.assertEqual(select_backend({"SANDBOX_DESCENDANT_SUPERVISOR": pass_evidence()}), V05Backend.DESCENDANT_SUPERVISOR)

    def test_controller_command_exec_is_never_a_candidate(self) -> None:
        evidence = {"CONTROLLER_COMMAND_EXEC": pass_evidence()}
        self.assertEqual(select_backend(evidence), V05Backend.CLI_RESUME_FALLBACK)

    def test_ownership_compare_and_swap_allows_one_background_owner(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-v05-test-") as temporary:
            ledger = OwnershipLedger(Path(temporary) / "ownership.json")
            pending = PendingHandoff(
                handoff_id="handoff",
                job_id="job",
                thread_id="thread",
                turn_id="turn",
                expected_command_sha256="hash",
                threshold_seconds=10,
                backend=V05Backend.THREAD_NATIVE_TERMINAL,
            )
            ledger.create(pending)
            ledger.transition("job", expected=OwnershipState.FOREGROUND, target=OwnershipState.HANDOFF_PENDING)
            ledger.transition(
                "job",
                expected=OwnershipState.HANDOFF_PENDING,
                target=OwnershipState.NATIVE_BACKGROUND,
                backend=V05Backend.THREAD_NATIVE_TERMINAL,
            )
            with self.assertRaises(ValueError):
                ledger.transition(
                    "job",
                    expected=OwnershipState.HANDOFF_PENDING,
                    target=OwnershipState.SUPERVISOR_BACKGROUND,
                    backend=V05Backend.DESCENDANT_SUPERVISOR,
                )

    def test_ownership_cannot_change_backend(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-v05-test-") as temporary:
            ledger = OwnershipLedger(Path(temporary) / "ownership.json")
            pending = PendingHandoff("h", "job", "thread", "turn", "hash", 10, V05Backend.THREAD_NATIVE_TERMINAL)
            ledger.create(pending)
            ledger.transition("job", expected=OwnershipState.FOREGROUND, target=OwnershipState.HANDOFF_PENDING)
            with self.assertRaises(ValueError):
                ledger.transition(
                    "job",
                    expected=OwnershipState.HANDOFF_PENDING,
                    target=OwnershipState.SUPERVISOR_BACKGROUND,
                    backend=V05Backend.DESCENDANT_SUPERVISOR,
                )

    def test_terminal_transition_is_durable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-v05-test-") as temporary:
            path = Path(temporary) / "ownership.json"
            ledger = OwnershipLedger(path)
            pending = PendingHandoff("h", "job", "thread", "turn", "hash", 10, V05Backend.THREAD_NATIVE_TERMINAL)
            ledger.create(pending)
            ledger.transition("job", expected=OwnershipState.FOREGROUND, target=OwnershipState.HANDOFF_PENDING)
            ledger.transition("job", expected=OwnershipState.HANDOFF_PENDING, target=OwnershipState.NATIVE_BACKGROUND, backend=V05Backend.THREAD_NATIVE_TERMINAL)
            ledger.transition("job", expected=OwnershipState.NATIVE_BACKGROUND, target=OwnershipState.COMPLETING)
            ledger.transition("job", expected=OwnershipState.COMPLETING, target=OwnershipState.COMPLETED)
            self.assertEqual(json.loads(path.read_text())["jobs"]["job"]["state"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
