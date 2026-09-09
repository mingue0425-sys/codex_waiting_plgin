from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller import DeliveryController
from snooze_controller.controller import DeliveryControllerError
from snooze_controller.gate import choose_architecture
from snooze_core.cli import command_from_args, select_shell
from snooze_core.git_fingerprint import changed, fingerprint
from snooze_core.logging import BoundedLogSet
from snooze_core.models import (
    DeliveryState,
    ExecutionState,
    JobSpec,
    LogLimits,
    result_hash_matches,
)
from snooze_core.process_identity import identity_matches, process_identity
from snooze_core.recovery import recover_store
from snooze_core.store import JobStore


class CoreTests(unittest.TestCase):
    def new_store(self, temporary: str) -> JobStore:
        return JobStore(Path(temporary) / "store")

    def new_job(
        self,
        store: JobStore,
        command: str = "printf test",
        shell: str = "/bin/bash",
        cwd: Path | None = None,
    ) -> str:
        spec = JobSpec.create(command=command, cwd=cwd or ROOT, shell=shell)
        store.create(spec)
        return spec.job_id

    def run_supervisor(self, store: JobStore, job_id: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
        if extra_env:
            environment.update(extra_env)
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "snooze_core.supervisor",
                "--run",
                job_id,
                "--store",
                str(store.root),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def test_jobspec_hash_and_command_reconstruction(self) -> None:
        spec = JobSpec.create("printf 'a b'", ROOT, "/bin/bash")
        self.assertEqual(spec.digest(), JobSpec(**spec.as_dict()).digest())
        self.assertNotEqual(spec.digest(), JobSpec(**{**spec.as_dict(), "command": "changed"}).digest())
        self.assertEqual(command_from_args(None, ["--", "echo", "a b", "&&", "true"]), "echo 'a b' && true")

    def test_state_machines_reject_invalid_skips(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-state-") as temporary:
            store = self.new_store(temporary)
            job_id = self.new_job(store)
            with self.assertRaises(ValueError):
                store.update_metadata(job_id, execution_state=ExecutionState.COMPLETED.value)
            with self.assertRaises(ValueError):
                store.update_delivery(job_id, state=DeliveryState.ACKED.value)

    def test_log_limits_preserve_execution_and_bound_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-log-") as temporary:
            log_set = BoundedLogSet(
                Path(temporary),
                LogLimits(max_log_bytes=32, max_preview_bytes=8, max_preview_lines=2, max_single_line_bytes=4),
            )
            log_set.write("stdout", b"123456789\n")
            log_set.write("stderr", b"abcdef\n")
            info = log_set.close()
            self.assertEqual(info.state.value, "TRUNCATED")
            self.assertTrue(info.stdout_truncated or info.stderr_truncated or info.combined_truncated)
            self.assertLessEqual(len((Path(temporary) / "stdout.log").read_bytes()), 32)
            self.assertLessEqual(len((Path(temporary) / "stderr.log").read_bytes()), 32)
            self.assertLessEqual(len(log_set.preview().encode()), 8)

    def test_process_identity_rejects_changed_fingerprint(self) -> None:
        identity = process_identity(os.getpid())
        self.assertTrue(identity_matches(identity))
        identity.command_fingerprint = "definitely-not-this-process"
        self.assertFalse(identity_matches(identity))

    def test_git_fingerprint_detects_worktree_change(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-git-") as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True, text=True)
            before = fingerprint(repo)
            (repo / "file.txt").write_text("changed\n", encoding="utf-8")
            after = fingerprint(repo)
            self.assertEqual(before["kind"], "GIT")
            self.assertTrue(changed(before, after))

    def test_supervisor_records_exact_exit_and_separate_logs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-run-") as temporary:
            store = self.new_store(temporary)
            job_id = self.new_job(store, "printf OUT; printf ERR >&2; exit 7")
            completed = self.run_supervisor(store, job_id)
            self.assertEqual(completed.returncode, 7)
            result = store.read_result(job_id)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result["execution_state"], ExecutionState.FAILED.value)
            self.assertEqual(result["exit_code"], 7)
            directory = store.job_dir(job_id)
            self.assertEqual((directory / "stdout.log").read_text(), "OUT")
            self.assertEqual((directory / "stderr.log").read_text(), "ERR")
            self.assertTrue(result_hash_matches(result))
            for name in ("spec.json", "metadata.json", "delivery.json", "stdout.log", "stderr.log", "combined.log", "supervisor.log"):
                self.assertEqual(stat.S_IMODE((directory / name).stat().st_mode), 0o600, name)

    def test_supervisor_refuses_metadata_command_tampering(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-tamper-") as temporary:
            store = self.new_store(temporary)
            job_id = self.new_job(store, "printf SHOULD_NOT_RUN")
            store.update_metadata(job_id, command="printf TAMPERED")
            completed = self.run_supervisor(store, job_id)
            self.assertEqual(completed.returncode, 125)
            result = store.read_result(job_id)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result["error_code"], "JOB_SPEC_TAMPERED")
            self.assertEqual((store.job_dir(job_id) / "stdout.log").read_bytes(), b"")

    def test_supervisor_is_the_shell_parent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-parent-") as temporary:
            store = self.new_store(temporary)
            job_id = self.new_job(store, 'printf "$PPID"')
            self.assertEqual(self.run_supervisor(store, job_id).returncode, 0)
            metadata = store.read_metadata(job_id)
            self.assertEqual(
                (store.job_dir(job_id) / "stdout.log").read_text(),
                str(metadata["supervisor_pid"]),
            )

    def test_supervisor_shell_operators_are_not_split(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-shell-") as temporary:
            store = self.new_store(temporary)
            job_id = self.new_job(
                store,
                "printf first | tr a-z A-Z > output.txt && cat output.txt",
                cwd=Path(temporary),
            )
            completed = self.run_supervisor(store, job_id)
            self.assertEqual(completed.returncode, 0)
            self.assertEqual((Path(temporary) / "output.txt").read_text(), "FIRST")

    def test_zsh_shell_contract_when_available(self) -> None:
        zsh = Path("/bin/zsh")
        if not zsh.is_file():
            self.skipTest("zsh is not installed")
        self.assertEqual(select_shell(str(zsh)), zsh.resolve())
        with tempfile.TemporaryDirectory(prefix="snooze-zsh-") as temporary:
            store = self.new_store(temporary)
            job_id = self.new_job(store, "value=$(printf nested); printf %s $value", shell=str(zsh))
            self.assertEqual(self.run_supervisor(store, job_id).returncode, 0)
            self.assertEqual((store.job_dir(job_id) / "stdout.log").read_text(), "nested")

    def test_project_change_marks_success_as_stale(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-stale-") as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True, text=True)
            (repo / "tracked.txt").write_text("before\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True, capture_output=True, text=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "-c",
                    "user.name=Codex Snooze Test",
                    "-c",
                    "user.email=test@example.invalid",
                    "commit",
                    "-q",
                    "-m",
                    "initial",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            store = self.new_store(temporary)
            job_id = self.new_job(store, "printf after > tracked.txt", cwd=repo)
            self.assertEqual(self.run_supervisor(store, job_id).returncode, 0)
            result = store.read_result(job_id)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result["execution_state"], ExecutionState.COMPLETED_STALE.value)
            self.assertTrue(result["source_state_changed"])

    def test_cancel_records_signal_and_keeps_result_delivery_separate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-cancel-") as temporary:
            store = self.new_store(temporary)
            job_id = self.new_job(store, "sleep 5")
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
            supervisor = subprocess.Popen(
                [sys.executable, "-m", "snooze_core.supervisor", "--run", job_id, "--store", str(store.root)],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                metadata = store.read_metadata(job_id)
                if metadata.get("execution_state") == ExecutionState.RUNNING.value:
                    break
                time.sleep(0.05)
            cancel = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "snooze_core.supervisor",
                    "--cancel",
                    job_id,
                    "--store",
                    str(store.root),
                    "--grace-seconds",
                    "1",
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            supervisor.communicate(timeout=5)
            self.assertEqual(cancel.returncode, 0, cancel.stderr)
            result = store.read_result(job_id)
            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result["execution_state"], ExecutionState.CANCELLED.value)
            self.assertEqual(result["termination_signal"], 15)
            self.assertEqual(store.read_delivery(job_id)["state"], DeliveryState.PENDING.value)

    def test_handoff_returns_while_supervisor_continues(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-handoff-") as temporary:
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "snooze_core",
                    "--store",
                    str(Path(temporary) / "store"),
                    "submit",
                    "--shell",
                    "/bin/bash",
                    "--handoff-after",
                    "0.05",
                    "--command",
                    "sleep 0.25; printf HANDOFF",
                ],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            self.assertEqual(completed.returncode, 0)
            handoff = json.loads(completed.stdout)
            self.assertTrue(handoff["handoff"])
            store = JobStore(Path(temporary) / "store")
            deadline = time.monotonic() + 3.0
            result = None
            while result is None and time.monotonic() < deadline:
                result = store.read_result(handoff["job_id"])
                if result is None:
                    time.sleep(0.05)
            self.assertIsNotNone(result)
            self.assertEqual(result["exit_code"], 0)

    def test_result_fault_is_reconciled_after_supervisor_crash(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-recovery-") as temporary:
            store = self.new_store(temporary)
            job_id = self.new_job(store, "printf RECOVERED")
            completed = self.run_supervisor(
                store,
                job_id,
                {"SNOOZE_FAULT_POINT": "after_rename", "SNOOZE_FAULT_TARGET": "result.json"},
            )
            self.assertEqual(completed.returncode, 75)
            self.assertIsNotNone(store.read_result(job_id))
            reports = recover_store(store.root, job_id)
            self.assertEqual(reports[0]["after_execution_state"], ExecutionState.COMPLETED.value)
            metadata = store.read_metadata(job_id)
            self.assertEqual(metadata["execution_state"], ExecutionState.COMPLETED.value)
            self.assertIsNotNone(metadata["finish_time"])
            self.assertEqual(store.read_delivery(job_id)["state"], DeliveryState.PENDING.value)

    def test_delivery_fault_becomes_sent_unconfirmed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-delivery-recovery-") as temporary:
            store = self.new_store(temporary)
            job_id = self.new_job(store, "printf DELIVERY")
            self.assertEqual(self.run_supervisor(store, job_id).returncode, 0)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
            environment["SNOOZE_FAULT_POINT"] = "after_rename"
            environment["SNOOZE_FAULT_TARGET"] = "delivery_payload.json"
            completed = subprocess.run(
                [sys.executable, "-m", "snooze_core", "--store", str(store.root), "deliver", job_id],
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(completed.returncode, 75)
            report = recover_store(store.root, job_id)[0]
            self.assertTrue(report.get("delivery_repaired"))
            self.assertEqual(store.read_delivery(job_id)["state"], DeliveryState.SENT_UNCONFIRMED.value)

    def test_delivery_ack_and_duplicate_guard(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-delivery-") as temporary:
            store = self.new_store(temporary)
            job_id = self.new_job(store, "printf DELIVERY")
            self.assertEqual(self.run_supervisor(store, job_id).returncode, 0)
            controller = DeliveryController(store)
            first = controller.deliver_manual(job_id)
            self.assertEqual(first["state"], DeliveryState.SENT_UNCONFIRMED.value)
            second = controller.deliver_manual(job_id)
            self.assertEqual(second["action"], "ack_or_explicitly_retry")
            with self.assertRaises(DeliveryControllerError):
                controller.retry(job_id)
            controller.acknowledge(job_id, event_id=first["event_id"])
            self.assertEqual(store.read_delivery(job_id)["state"], DeliveryState.ACKED.value)

    def test_cli_resume_is_explicit_and_stays_unconfirmed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-resume-") as temporary:
            store = self.new_store(temporary)
            job_id = self.new_job(store, "printf RESUME")
            self.assertEqual(self.run_supervisor(store, job_id).returncode, 0)
            controller = DeliveryController(store)
            fake = subprocess.CompletedProcess(
                ["codex"],
                0,
                '{"type":"turn.completed"}\n',
                "",
            )
            with patch("snooze_controller.controller.subprocess.run", return_value=fake) as run:
                value = controller.resume_cli(job_id, "session-123", timeout_seconds=2)
            self.assertEqual(value["state"], DeliveryState.SENT_UNCONFIRMED.value)
            self.assertEqual(store.read_delivery(job_id)["transport"], "codex-exec-resume")
            self.assertEqual(run.call_args.kwargs["cwd"], str(ROOT))
            self.assertIn("Do not rerun", run.call_args.args[0][-1])

    def test_capability_gate_refuses_unverified_desktop_integration(self) -> None:
        payload = {
            "capabilities": [
                {
                    "name": "codex_exec_resume",
                    "status": "PASS",
                    "observations": {
                        "conversation_history_restored": True,
                        "desktop_ui_same_thread": "UNKNOWN",
                        "sandbox_policy_restored": "UNKNOWN",
                        "approval_policy_restored": "UNKNOWN",
                    },
                },
                {"name": "snooze_turn_interrupt_survival", "status": "PASS"},
            ]
        }
        self.assertEqual(choose_architecture(payload), "CLI_RESUME_FALLBACK")

    def test_capability_gate_requires_busy_delivery_proof_for_direct_path(self) -> None:
        payload = {
            "capabilities": [
                {
                    "name": "codex_exec_resume",
                    "status": "PASS",
                    "observations": {
                        "conversation_history_restored": True,
                        "desktop_ui_same_thread": True,
                        "sandbox_policy_restored": True,
                        "approval_policy_restored": True,
                    },
                },
                {"name": "turn_interrupt", "status": "PASS"},
                {"name": "snooze_turn_interrupt_survival", "status": "PASS"},
                {"name": "busy_thread_delivery", "status": "UNKNOWN"},
            ]
        }
        self.assertEqual(choose_architecture(payload), "CLI_RESUME_FALLBACK")

    def test_capability_gate_selects_direct_only_after_all_proofs_pass(self) -> None:
        payload = {
            "capabilities": [
                {
                    "name": "codex_exec_resume",
                    "status": "PASS",
                    "observations": {
                        "conversation_history_restored": True,
                        "desktop_ui_same_thread": True,
                        "sandbox_policy_restored": True,
                        "approval_policy_restored": True,
                    },
                },
                {"name": "turn_interrupt", "status": "PASS"},
                {"name": "snooze_turn_interrupt_survival", "status": "PASS"},
                {"name": "busy_thread_delivery", "status": "PASS"},
            ]
        }
        self.assertEqual(choose_architecture(payload), "APP_SERVER_DIRECT")

    def test_recovery_does_not_claim_a_live_job(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snooze-live-") as temporary:
            store = self.new_store(temporary)
            job_id = self.new_job(store, "sleep 0.5")
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
            process = subprocess.Popen(
                [sys.executable, "-m", "snooze_core.supervisor", "--run", job_id, "--store", str(store.root)],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if store.read_metadata(job_id).get("execution_state") == ExecutionState.RUNNING.value:
                    break
                time.sleep(0.05)
            report = recover_store(store.root, job_id)[0]
            self.assertEqual(report["action"], "still_running")
            process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
