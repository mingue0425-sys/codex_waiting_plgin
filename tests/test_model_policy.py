from __future__ import annotations

import unittest

from snooze_controller.model_policy import (
    LUNA_MODEL,
    REASONING_EFFORT,
    ModelPolicyError,
    attest_model,
    attest_exec_events,
    ensure_luna_app_server_command,
    ensure_luna_exec_command,
    luna_thread_params,
    luna_turn_params,
)


class ModelPolicyTests(unittest.TestCase):
    def test_app_server_command_is_explicit(self) -> None:
        self.assertEqual(
            ensure_luna_app_server_command(["codex", "app-server", "--stdio"]),
            [
                "codex",
                "-m",
                LUNA_MODEL,
                "-c",
                'model_reasoning_effort="medium"',
                "app-server",
                "--stdio",
            ],
        )

    def test_exec_command_is_explicit(self) -> None:
        command = ensure_luna_exec_command(["codex", "exec", "--json", "prompt"])
        self.assertEqual(command[0:6], ["codex", "exec", "-m", LUNA_MODEL, "-c", 'model_reasoning_effort="medium"'])

    def test_non_luna_command_fails_closed(self) -> None:
        with self.assertRaises(ModelPolicyError):
            ensure_luna_exec_command(["codex", "exec", "-m", "gpt-5.6-sol", "prompt"])

    def test_config_equals_form_is_validated(self) -> None:
        with self.assertRaises(ModelPolicyError):
            ensure_luna_exec_command(["codex", "exec", "--config=model=gpt-5.6-sol", "prompt"])
        command = ensure_luna_exec_command(["codex", "exec", "--config=approval_policy=never", "prompt"])
        self.assertIn("-m", command)
        self.assertIn('model_reasoning_effort="medium"', command)

    def test_requests_use_schema_field_names(self) -> None:
        thread = luna_thread_params({"cwd": "/workspace"})
        turn = luna_turn_params({"threadId": "thread", "input": []})
        self.assertEqual(thread["model"], LUNA_MODEL)
        self.assertEqual(turn["model"], LUNA_MODEL)
        self.assertEqual(turn["effort"], REASONING_EFFORT)
        self.assertEqual(thread["approvalsReviewer"], "user")
        self.assertEqual(turn["multiAgentMode"], {"custom": "disabled"})

    def test_missing_runtime_field_fails_closed(self) -> None:
        value = attest_model(
            requested_model=LUNA_MODEL,
            runtime_reported_model=LUNA_MODEL,
            thread_model=LUNA_MODEL,
            turn_model=None,
            reasoning_effort=REASONING_EFFORT,
        )
        self.assertEqual(value.status, "FAIL")
        self.assertFalse(value.verified)
        self.assertIn("missing_turn_model", value.reasons)

    def test_complete_luna_attestation_passes(self) -> None:
        value = attest_model(
            requested_model=LUNA_MODEL,
            runtime_reported_model=LUNA_MODEL,
            thread_model=LUNA_MODEL,
            turn_model=LUNA_MODEL,
            reasoning_effort=REASONING_EFFORT,
        )
        self.assertEqual(value.status, "PASS")
        self.assertTrue(value.as_dict(experiment_valid=True)["experiment_valid"])

    def test_exec_reroute_event_fails_closed(self) -> None:
        value = attest_exec_events(
            [
                {"type": "thread.started", "model": LUNA_MODEL},
                {"type": "turn.started", "model": LUNA_MODEL},
                {"type": "model.rerouted", "model": LUNA_MODEL},
            ]
        )
        self.assertEqual(value.status, "FAIL")
        self.assertIn("automatic_fallback_detected", value.reasons)


if __name__ == "__main__":
    unittest.main()
