from __future__ import annotations

"""Durable ownership records for Snooze-created App Server threads."""

import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from snooze_core.models import utc_now
from snooze_core.persistence import atomic_write_json


class ThreadRegistry:
    """Persist only Snooze-owned thread metadata, never Desktop ownership."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _read(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": 1, "threads": {}}
        import json

        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("threads", {}), dict):
            raise ValueError("thread registry is not a valid object")
        value.setdefault("schema_version", 1)
        return value

    def _write(self, value: Dict[str, Any]) -> None:
        atomic_write_json(self.path, value)

    def register(
        self,
        thread_id: str,
        *,
        app_server_instance: str,
        cwd: Path,
        sandbox: Any = None,
        approval_policy: Any = None,
        model: Optional[str] = None,
        source: str = "codex-app-server",
    ) -> Dict[str, Any]:
        if not thread_id or not app_server_instance:
            raise ValueError("thread id and App Server instance are required")
        record = {
            "thread_id": thread_id,
            "owner": "codex-snooze",
            "app_server_instance": app_server_instance,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "cwd": str(Path(cwd).resolve()),
            "sandbox": sandbox,
            "approval_policy": approval_policy,
            "model": model,
            "source": source,
            "state": "LIVE",
            "turns": [],
            "handoffs": [],
        }
        with self._lock:
            value = self._read()
            value["threads"][thread_id] = record
            self._write(value)
        return dict(record)

    def get(self, thread_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._read().get("threads", {}).get(thread_id)

    def update(self, thread_id: str, **fields: Any) -> Dict[str, Any]:
        with self._lock:
            value = self._read()
            current = value.get("threads", {}).get(thread_id)
            if current is None:
                raise KeyError(thread_id)
            current.update(fields)
            current["updated_at"] = utc_now()
            value["threads"][thread_id] = current
            self._write(value)
            return dict(current)

    def append_turn(self, thread_id: str, turn_id: str, *, status: str = "STARTED") -> Dict[str, Any]:
        record = self.get(thread_id)
        if record is None:
            raise KeyError(thread_id)
        turns = list(record.get("turns", []))
        turns.append({"turn_id": turn_id, "status": status, "started_at": utc_now()})
        return self.update(thread_id, turns=turns)

    def update_turn(self, thread_id: str, turn_id: str, **fields: Any) -> Dict[str, Any]:
        record = self.get(thread_id)
        if record is None:
            raise KeyError(thread_id)
        turns = list(record.get("turns", []))
        for turn in turns:
            if turn.get("turn_id") == turn_id:
                turn.update(fields)
                turn["updated_at"] = utc_now()
                break
        else:
            turns.append({"turn_id": turn_id, **fields, "updated_at": utc_now()})
        return self.update(thread_id, turns=turns)

    def mark_state(self, thread_id: str, state: str, *, app_server_instance: Optional[str] = None) -> Dict[str, Any]:
        fields: Dict[str, Any] = {"state": state}
        if app_server_instance is not None:
            fields["app_server_instance"] = app_server_instance
        return self.update(thread_id, **fields)

    def record_handoff(
        self,
        thread_id: str,
        *,
        turn_id: str,
        item_id: Optional[str],
        job_id: str,
        marker: Dict[str, Any],
        state: str = "HANDOFF_REQUESTED",
    ) -> Dict[str, Any]:
        """Atomically bind a detached job to its live thread and turn."""
        with self._lock:
            value = self._read()
            current = value.get("threads", {}).get(thread_id)
            if current is None:
                raise KeyError(thread_id)
            handoffs = list(current.get("handoffs", []))
            for existing in handoffs:
                if existing.get("job_id") == job_id:
                    if existing.get("turn_id") != turn_id or existing.get("thread_id") != thread_id:
                        raise ValueError("job is already mapped to another thread or turn")
                    return dict(existing)
            entry = {
                "thread_id": thread_id,
                "turn_id": turn_id,
                "item_id": item_id,
                "job_id": job_id,
                "event_id": marker.get("completion_event_id"),
                "marker": dict(marker),
                "state": state,
                "created_at": utc_now(),
                "updated_at": utc_now(),
            }
            handoffs.append(entry)
            current["handoffs"] = handoffs
            current["updated_at"] = utc_now()
            value["threads"][thread_id] = current
            self._write(value)
            return dict(entry)

    def update_handoff(self, thread_id: str, job_id: str, **fields: Any) -> Dict[str, Any]:
        with self._lock:
            value = self._read()
            current = value.get("threads", {}).get(thread_id)
            if current is None:
                raise KeyError(thread_id)
            for entry in current.setdefault("handoffs", []):
                if entry.get("job_id") == job_id:
                    entry.update(fields)
                    entry["updated_at"] = utc_now()
                    current["updated_at"] = utc_now()
                    value["threads"][thread_id] = current
                    self._write(value)
                    return dict(entry)
            raise KeyError(f"handoff {thread_id}/{job_id}")

    def find_handoff(self, *, thread_id: Optional[str] = None, job_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        with self._lock:
            for record in self._read().get("threads", {}).values():
                if thread_id is not None and record.get("thread_id") != thread_id:
                    continue
                for entry in record.get("handoffs", []):
                    if job_id is None or entry.get("job_id") == job_id:
                        return dict(entry)
        return None

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._read().get("threads", {}).values()]


__all__ = ["ThreadRegistry"]
