from __future__ import annotations

"""At-least-once completion routing for owned App Server threads.

The router persists an event marker before starting a continuation.  An App
Server response does not provide a client idempotency key in the installed
protocol, so ``SENT_UNCONFIRMED`` remains an explicit state and exactly-once
delivery is never claimed.
"""

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from snooze_core.models import utc_now
from snooze_core.persistence import atomic_write_json

from .agent import AgentController, AgentControllerError


class CompletionRouterError(RuntimeError):
    pass


class CompletionRouter:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _read(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "events": {}}
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("events", {}), dict):
            raise ValueError("completion router state is not a valid object")
        return value

    def _write(self, value: Dict[str, Any]) -> None:
        atomic_write_json(self.path, value)

    def get(self, event_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._read().get("events", {}).get(event_id)

    def claim(
        self,
        *,
        event_id: str,
        job_id: str,
        thread_id: str,
        result_sha256: Optional[str],
        allow_duplicate: bool = False,
    ) -> Dict[str, Any]:
        if not event_id or not job_id or not thread_id:
            raise CompletionRouterError("event, job and thread ids are required")
        with self._lock:
            value = self._read()
            existing = value["events"].get(event_id)
            if existing is not None:
                if existing.get("job_id") != job_id or existing.get("thread_id") != thread_id:
                    raise CompletionRouterError("event id is already bound to another job or thread")
                if existing.get("state") == "ACKED":
                    raise CompletionRouterError("completion event is already ACKED")
                if existing.get("state") in {"CLAIMED", "STARTING", "SENDING"} and not allow_duplicate:
                    raise CompletionRouterError("completion event is already in flight")
                if existing.get("state") == "SENT_UNCONFIRMED" and not allow_duplicate:
                    raise CompletionRouterError("completion event is SENT_UNCONFIRMED; duplicate permission is required")
                attempts = int(existing.get("attempts", 0)) + 1
                existing.update(
                    {
                        "state": "CLAIMED",
                        "attempts": attempts,
                        "claimed_at": utc_now(),
                        "result_sha256": result_sha256,
                    }
                )
                value["events"][event_id] = existing
            else:
                existing = {
                    "schema_version": 1,
                    "event_id": event_id,
                    "job_id": job_id,
                    "thread_id": thread_id,
                    "result_sha256": result_sha256,
                    "state": "CLAIMED",
                    "attempts": 1,
                    "claimed_at": utc_now(),
                    "started_at": None,
                    "sent_at": None,
                    "acked_at": None,
                    "error": None,
                }
                value["events"][event_id] = existing
            self._write(value)
            return dict(existing)

    def route(
        self,
        controller: AgentController,
        *,
        event_id: str,
        job_id: str,
        result_sha256: Optional[str],
        prompt: str,
        allow_duplicate: bool = False,
        request_timeout: float = 30.0,
        wait_timeout: float = 300.0,
    ) -> Dict[str, Any]:
        thread_id = controller.thread_id
        if not thread_id:
            raise CompletionRouterError("controller has no live thread")
        record = self.claim(
            event_id=event_id,
            job_id=job_id,
            thread_id=thread_id,
            result_sha256=result_sha256,
            allow_duplicate=allow_duplicate,
        )
        with self._lock:
            value = self._read()
            current = value["events"][event_id]
            current["state"] = "STARTING"
            current["started_at"] = utc_now()
            self._write(value)
        enriched = (
            f"Codex Snooze completion event {event_id}.\n"
            f"job_id={job_id}\n"
            f"result_sha256={result_sha256}\n"
            "Do not rerun the completed command.\n"
            + prompt
        )
        try:
            turn = controller.start_turn(enriched, timeout=request_timeout)
            with self._lock:
                value = self._read()
                current = value["events"][event_id]
                current["state"] = "SENT_UNCONFIRMED"
                current["sent_at"] = utc_now()
                current["turn_id"] = turn.get("id")
                current["error"] = None
                self._write(value)
            try:
                completed = controller.wait_turn(turn.get("id"), timeout=wait_timeout)
            except AgentControllerError as exc:
                self.mark_retry(event_id, str(exc))
                raise
            return {
                "event_id": event_id,
                "job_id": job_id,
                "thread_id": thread_id,
                "turn_id": turn.get("id"),
                "state": "SENT_UNCONFIRMED",
                "turn": completed,
                "attempts": record.get("attempts"),
            }
        except (AgentControllerError, OSError, RuntimeError) as exc:
            self.mark_retry(event_id, f"{type(exc).__name__}: {exc}")
            raise CompletionRouterError(str(exc)) from exc

    def mark_retry(self, event_id: str, error: str) -> Dict[str, Any]:
        with self._lock:
            value = self._read()
            record = value.get("events", {}).get(event_id)
            if record is None:
                raise CompletionRouterError("unknown completion event")
            record["state"] = "RETRY_WAIT"
            record["error"] = error
            record["updated_at"] = utc_now()
            self._write(value)
            return dict(record)

    def acknowledge(self, event_id: str, *, turn_id: Optional[str] = None) -> Dict[str, Any]:
        with self._lock:
            value = self._read()
            record = value.get("events", {}).get(event_id)
            if record is None:
                raise CompletionRouterError("unknown completion event")
            if record.get("state") == "ACKED":
                return dict(record)
            if record.get("state") != "SENT_UNCONFIRMED":
                raise CompletionRouterError(f"event is {record.get('state')}, expected SENT_UNCONFIRMED")
            if turn_id is not None and record.get("turn_id") != turn_id:
                raise CompletionRouterError("turn id does not match the recorded continuation")
            record["state"] = "ACKED"
            record["acked_at"] = utc_now()
            record["error"] = None
            self._write(value)
            return dict(record)

    def retry(self, event_id: str, *, allow_duplicate: bool = False) -> Dict[str, Any]:
        with self._lock:
            value = self._read()
            record = value.get("events", {}).get(event_id)
            if record is None:
                raise CompletionRouterError("unknown completion event")
            if record.get("state") == "ACKED":
                raise CompletionRouterError("ACKED event cannot be retried")
            if record.get("state") == "SENT_UNCONFIRMED" and not allow_duplicate:
                raise CompletionRouterError("SENT_UNCONFIRMED may duplicate; explicit permission is required")
            record["state"] = "RETRY_WAIT"
            record["retry_requested_at"] = utc_now()
            self._write(value)
            return dict(record)


__all__ = ["CompletionRouter", "CompletionRouterError"]
