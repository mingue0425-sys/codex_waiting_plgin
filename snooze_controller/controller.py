from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict

from snooze_core.models import DeliveryState, result_hash_matches, utc_now
from snooze_core.persistence import atomic_write_json
from snooze_core.store import JobStore


class DeliveryControllerError(RuntimeError):
    pass


class DeliveryController:
    """Manage recoverable, at-least-once-compatible completion delivery."""

    def __init__(self, store: JobStore):
        self.store = store

    def _event_payload(self, job_id: str) -> Dict[str, Any]:
        result = self.store.read_result(job_id)
        if result is None:
            raise DeliveryControllerError("result is not available")
        if not result_hash_matches(result):
            raise DeliveryControllerError("result_sha256 does not match result.json")
        return {
            "schema_version": 1,
            "event_id": result["completion_event_id"],
            "job_id": job_id,
            "result_sha256": result["result_sha256"],
            "completed_at": result.get("completed_at"),
            "execution_state": result.get("execution_state"),
            "result_state": result.get("result_state"),
            "exit_code": result.get("exit_code"),
            "termination_signal": result.get("termination_signal"),
            "duration_seconds": result.get("duration_seconds"),
            "source_state_changed": result.get("source_state_changed"),
            "log_preview": result.get("log_preview", ""),
            "result_path": str(self.store.job_dir(job_id) / "result.json"),
        }

    def _prepare_delivery(
        self,
        job_id: str,
        transport: str,
        allow_duplicate: bool = False,
    ) -> tuple[Dict[str, Any], int, Path, DeliveryState]:
        result = self.store.read_result(job_id)
        if result is None:
            raise DeliveryControllerError("result is not available")
        if not result_hash_matches(result):
            raise DeliveryControllerError("result_sha256 does not match result.json")
        delivery = self.store.read_delivery(job_id)
        state = DeliveryState(delivery["state"])
        if state == DeliveryState.ACKED:
            raise DeliveryControllerError("delivery is already ACKED")
        if state == DeliveryState.SENT_UNCONFIRMED and not allow_duplicate:
            raise DeliveryControllerError("delivery is SENT_UNCONFIRMED; explicit duplicate permission is required")
        if state not in {
            DeliveryState.PENDING,
            DeliveryState.RETRY_WAIT,
            DeliveryState.FAILED,
            DeliveryState.SENT_UNCONFIRMED,
        }:
            raise DeliveryControllerError(f"delivery is currently {state.value}")
        now = utc_now()
        allowed_states = {
            DeliveryState.PENDING,
            DeliveryState.RETRY_WAIT,
            DeliveryState.FAILED,
        }
        if allow_duplicate:
            allowed_states.add(DeliveryState.SENT_UNCONFIRMED)
        claimed = self.store.claim_delivery(
            job_id,
            allowed_states,
            last_attempt_at=now,
            transport=transport,
            error_code=None,
            error_message=None,
        )
        attempts = int(claimed["attempts"])
        self.store.update_delivery(job_id, state=DeliveryState.SENDING.value)
        payload = self._event_payload(job_id)
        payload["transport"] = transport
        payload_path = self.store.job_dir(job_id) / "delivery_payload.json"
        atomic_write_json(payload_path, payload)
        return payload, attempts, payload_path, state

    def deliver_manual(self, job_id: str, allow_duplicate: bool = False) -> Dict[str, Any]:
        delivery = self.store.read_delivery(job_id)
        state = DeliveryState(delivery["state"])
        if state == DeliveryState.ACKED:
            return {"job_id": job_id, "state": state.value, "duplicate": False}
        if state == DeliveryState.SENT_UNCONFIRMED and not allow_duplicate:
            return {
                "job_id": job_id,
                "state": state.value,
                "duplicate": False,
                "action": "ack_or_explicitly_retry",
            }
        payload, attempts, payload_path, previous_state = self._prepare_delivery(
            job_id, "manual-file", allow_duplicate=allow_duplicate
        )
        self.store.update_delivery(
            job_id,
            state=DeliveryState.SENT_UNCONFIRMED.value,
            remote_marker=payload_path.name,
        )
        return {
            "job_id": job_id,
            "state": DeliveryState.SENT_UNCONFIRMED.value,
            "attempts": attempts,
            "payload_path": str(payload_path),
            "event_id": payload["event_id"],
            "duplicate": previous_state == DeliveryState.SENT_UNCONFIRMED,
        }

    def resume_cli(
        self,
        job_id: str,
        session_id: str,
        timeout_seconds: float = 300.0,
        allow_duplicate: bool = False,
    ) -> Dict[str, Any]:
        """Explicitly send a compact completion prompt through CLI resume.

        This is a user-invoked fallback.  It records SENT_UNCONFIRMED after the
        CLI exits successfully because a CLI return is not an acknowledgement
        from a Desktop thread.
        """
        if not session_id:
            raise DeliveryControllerError("session id is required")
        if timeout_seconds <= 0:
            raise DeliveryControllerError("timeout must be positive")
        payload, attempts, payload_path, previous_state = self._prepare_delivery(
            job_id,
            "codex-exec-resume",
            allow_duplicate=allow_duplicate,
        )
        prompt = (
            "Codex Snooze completion event: "
            + str(payload["event_id"])
            + "\n\n"
            + f"job={job_id}\n"
            + f"execution_state={payload['execution_state']}\n"
            + f"result_state={payload['result_state']}\n"
            + f"exit_code={payload['exit_code']}\n"
            + f"termination_signal={payload['termination_signal']}\n"
            + f"source_state_changed={payload['source_state_changed']}\n"
            + "Do not rerun the completed command.\n"
            + f"Full result metadata: {payload['result_path']}\n"
            + "Log preview:\n"
            + str(payload.get("log_preview", ""))
        )
        spec = self.store.read_spec(job_id)
        try:
            completed = subprocess.run(
                [
                    "codex",
                    "exec",
                    "resume",
                    "--json",
                    "--skip-git-repo-check",
                    session_id,
                    prompt,
                ],
                cwd=spec.cwd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
                env=None,
            )
            attempt = {
                "transport": "codex-exec-resume",
                "session_id": session_id,
                "returncode": completed.returncode,
                "stdout_tail": completed.stdout[-4000:],
                "stderr_tail": completed.stderr[-4000:],
                "at": utc_now(),
            }
            atomic_write_json(self.store.job_dir(job_id) / "delivery_attempt.json", attempt)
        except subprocess.TimeoutExpired as exc:
            attempt = {
                "transport": "codex-exec-resume",
                "session_id": session_id,
                "returncode": None,
                "error_code": "DELIVERY_TIMEOUT",
                "error_message": str(exc),
                "at": utc_now(),
            }
            atomic_write_json(self.store.job_dir(job_id) / "delivery_attempt.json", attempt)
            self.store.update_delivery(
                job_id,
                state=DeliveryState.RETRY_WAIT.value,
                error_code="DELIVERY_TIMEOUT",
                error_message=str(exc),
            )
            raise DeliveryControllerError("codex exec resume timed out") from exc
        except OSError as exc:
            attempt = {
                "transport": "codex-exec-resume",
                "session_id": session_id,
                "returncode": None,
                "error_code": "RESUME_SPAWN_FAILED",
                "error_message": str(exc),
                "at": utc_now(),
            }
            atomic_write_json(self.store.job_dir(job_id) / "delivery_attempt.json", attempt)
            self.store.update_delivery(
                job_id,
                state=DeliveryState.RETRY_WAIT.value,
                error_code="RESUME_SPAWN_FAILED",
                error_message=str(exc),
            )
            raise DeliveryControllerError(f"could not start codex exec resume: {exc}") from exc
        if completed.returncode == 0:
            self.store.update_delivery(
                job_id,
                state=DeliveryState.SENT_UNCONFIRMED.value,
                remote_marker=f"codex-exec-resume:{session_id}",
            )
        else:
            self.store.update_delivery(
                job_id,
                state=DeliveryState.RETRY_WAIT.value,
                error_code="RESUME_FAILED",
                error_message=f"codex exec resume returned {completed.returncode}",
            )
        return {
            "job_id": job_id,
            "state": self.store.read_delivery(job_id)["state"],
            "attempts": attempts,
            "event_id": payload["event_id"],
            "session_id": session_id,
            "returncode": completed.returncode,
            "payload_path": str(payload_path),
            "duplicate": previous_state == DeliveryState.SENT_UNCONFIRMED,
        }

    def acknowledge(self, job_id: str, event_id: str | None = None) -> Dict[str, Any]:
        delivery = self.store.read_delivery(job_id)
        state = DeliveryState(delivery["state"])
        if state == DeliveryState.ACKED:
            return delivery
        if state != DeliveryState.SENT_UNCONFIRMED:
            raise DeliveryControllerError(f"delivery must be SENT_UNCONFIRMED, got {state.value}")
        expected = delivery.get("completion_event_id")
        if event_id is not None and event_id != expected:
            raise DeliveryControllerError("event id does not match completion event")
        return self.store.update_delivery(
            job_id,
            state=DeliveryState.ACKED.value,
            acked_at=utc_now(),
            error_code=None,
            error_message=None,
        )

    def retry(self, job_id: str, allow_duplicate: bool = False) -> Dict[str, Any]:
        delivery = self.store.read_delivery(job_id)
        state = DeliveryState(delivery["state"])
        if state == DeliveryState.SENT_UNCONFIRMED and not allow_duplicate:
            raise DeliveryControllerError(
                "SENT_UNCONFIRMED may duplicate; pass allow_duplicate explicitly"
            )
        if state == DeliveryState.ACKED:
            raise DeliveryControllerError("ACKED delivery cannot be retried")
        if state not in {
            DeliveryState.SENT_UNCONFIRMED,
            DeliveryState.RETRY_WAIT,
            DeliveryState.FAILED,
            DeliveryState.CLAIMED,
            DeliveryState.SENDING,
        }:
            raise DeliveryControllerError(f"delivery is currently {state.value}")
        # In-flight states are first made retryable by an explicit operator
        # action.  This preserves the duplicate-delivery warning in the state.
        return self.store.update_delivery(
            job_id,
            state=DeliveryState.PENDING.value,
            error_code="EXPLICIT_RETRY",
            error_message="operator requested another at-least-once delivery attempt",
        )


__all__ = ["DeliveryController", "DeliveryControllerError"]
