from __future__ import annotations

"""Small JSON-RPC client used only by the v0.2 capability probes.

The client deliberately has no Codex-specific assumptions beyond newline
delimited JSON-RPC.  It records every request, response, notification and
stderr line so a probe can explain both successful and rejected operations.
"""

import json
import os
import queue
import re
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Sequence


_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|credential|cookie|password|secret|token|bearer|private[_-]?key)$",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(r"\b(?:sk|sess|tok|key)_[A-Za-z0-9._-]{12,}\b")
_PRIVATE_CONTENT_KEY = re.compile(r"^(?:preview|path)$", re.IGNORECASE)
_RUNTIME_PATH_PREFIXES = tuple(
    prefix
    for prefix in (str(Path.home()), os.environ.get("CODEX_HOME", ""))
    if prefix
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def redact(value: Any, key: str = "", *, max_string: int = 8000) -> Any:
    """Redact credential-shaped values and bound probe output size."""
    if _SENSITIVE_KEY.search(key):
        return "<redacted>"
    if _PRIVATE_CONTENT_KEY.search(key):
        return "<redacted-runtime-content>"
    if isinstance(value, dict):
        return {str(name): redact(item, str(name), max_string=max_string) for name, item in value.items()}
    if isinstance(value, list):
        return [redact(item, key, max_string=max_string) for item in value]
    if isinstance(value, tuple):
        return [redact(item, key, max_string=max_string) for item in value]
    if isinstance(value, str):
        value = _SENSITIVE_TEXT.sub("<redacted>", value)
        for prefix in _RUNTIME_PATH_PREFIXES:
            value = value.replace(prefix, "<redacted-home>")
        if len(value) > max_string:
            return value[:max_string] + "…<truncated>"
    return value


def response_error(response: Optional[Dict[str, Any]]) -> Optional[str]:
    if not response:
        return "no response"
    error = response.get("error")
    if error is None:
        return None
    return json.dumps(redact(error), ensure_ascii=False, separators=(",", ":"))


def extract_ids(value: Any) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(value, dict):
        return None, None
    thread_id = value.get("threadId") or value.get("thread_id")
    turn_id = value.get("turnId") or value.get("turn_id") or value.get("expectedTurnId")
    return (
        str(thread_id) if thread_id is not None else None,
        str(turn_id) if turn_id is not None else None,
    )


class AppServerClient:
    """A serialized JSON-RPC client with an inspectable event trace."""

    def __init__(
        self,
        command: Sequence[str] = ("codex", "app-server", "--stdio"),
        *,
        cwd: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
        stderr_tail_limit: int = 100,
    ) -> None:
        self.command = list(command)
        self.cwd = str(cwd) if cwd is not None else None
        self.process = subprocess.Popen(
            self.command,
            cwd=self.cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env or os.environ.copy(),
        )
        self._messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._pending_responses: Dict[Any, Dict[str, Any]] = {}
        self._request_lock = threading.Lock()
        self._events: List[Dict[str, Any]] = []
        self._notifications: List[Dict[str, Any]] = []
        self._stderr: Deque[str] = deque(maxlen=stderr_tail_limit)
        self._sequence = 0
        self._next_request_id = 1
        self._closed = False
        self._stdout_thread = threading.Thread(target=self._read_stdout, name="app-server-stdout", daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, name="app-server-stderr", daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    @property
    def events(self) -> List[Dict[str, Any]]:
        return list(self._events)

    @property
    def notifications(self) -> List[Dict[str, Any]]:
        return list(self._notifications)

    @property
    def stderr_tail(self) -> List[str]:
        return list(self._stderr)

    def _record(self, kind: str, **fields: Any) -> None:
        self._sequence += 1
        self._events.append(
            {
                "sequence": self._sequence,
                "timestamp": utc_now(),
                "monotonic_ns": time.monotonic_ns(),
                "kind": kind,
                **redact(fields),
            }
        )

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            raw = line.rstrip("\n")
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                self._record("stdout_parse_error", line=raw)
                self._messages.put(("text", raw))
                continue
            if not isinstance(value, dict):
                self._record("json_message", value=value)
                self._messages.put(("json", value))
                continue
            if "id" in value and ("result" in value or "error" in value):
                self._record(
                    "response",
                    request_id=value.get("id"),
                    result=value.get("result"),
                    error=value.get("error"),
                )
                self._messages.put(("response", value))
            elif "method" in value:
                thread_id, turn_id = extract_ids(value.get("params"))
                notification = {
                    "method": value.get("method"),
                    "params": redact(value.get("params")),
                }
                self._notifications.append(notification)
                self._record(
                    "notification",
                    method=value.get("method"),
                    thread_id=thread_id,
                    turn_id=turn_id,
                    params=value.get("params"),
                )
                self._messages.put(("notification", value))
            else:
                self._record("json_message", value=value)
                self._messages.put(("json", value))
        self._record("stdout_closed")

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            text = redact(line.rstrip("\n"))
            self._stderr.append(str(text))
            self._record("stderr", line=text)
        self._record("stderr_closed")

    def _send(self, message: Dict[str, Any], *, kind: str) -> None:
        if self._closed or self.process.stdin is None:
            raise RuntimeError("app-server client is closed")
        self.process.stdin.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        thread_id, turn_id = extract_ids(message.get("params"))
        self._record(
            kind,
            request_id=message.get("id"),
            method=message.get("method"),
            thread_id=thread_id,
            turn_id=turn_id,
            params=message.get("params"),
        )

    def notify(self, method: str, params: Any) -> None:
        self._send({"method": method, "params": params}, kind="notification_sent")

    def request(self, method: str, params: Any, timeout: float = 15.0) -> Dict[str, Any]:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        with self._request_lock:
            request_id = self._next_request_id
            self._next_request_id += 1
            self._send({"method": method, "id": request_id, "params": params}, kind="request")
            pending = self._pending_responses.pop(request_id, None)
            if pending is not None:
                return pending
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    kind, value = self._messages.get(
                        timeout=min(0.25, max(0.01, deadline - time.monotonic()))
                    )
                except queue.Empty:
                    continue
                if kind == "response":
                    if isinstance(value, dict) and value.get("id") == request_id:
                        return value
                    if isinstance(value, dict):
                        self._pending_responses[value.get("id")] = value
            raise TimeoutError(f"timed out waiting for app-server response {request_id} ({method})")

    def pump(self, timeout: float = 0.25) -> Optional[tuple[str, Any]]:
        try:
            message = self._messages.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None
        kind, value = message
        if kind == "response" and isinstance(value, dict):
            self._pending_responses[value.get("id")] = value
        return message

    def wait_for_notification(
        self,
        predicate: Callable[[Dict[str, Any]], bool],
        timeout: float = 15.0,
    ) -> Optional[Dict[str, Any]]:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        checked = 0
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            while checked < len(self._notifications):
                candidate = self._notifications[checked]
                checked += 1
                if predicate(candidate):
                    return candidate
            self.pump(min(0.25, max(0.01, deadline - time.monotonic())))
        while checked < len(self._notifications):
            candidate = self._notifications[checked]
            checked += 1
            if predicate(candidate):
                return candidate
        return None

    def snapshot(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "cwd": self.cwd,
            "returncode": self.process.poll(),
            "events": self.events,
            "notifications": self.notifications,
            "stderr_tail": self.stderr_tail,
        }

    def close(self, timeout: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._record("client_terminate", reason="stdio close timeout")
            self.process.terminate()
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._record("client_kill", reason="terminate timeout")
                self.process.kill()
                self.process.wait(timeout=timeout)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        self._record("process_exit", returncode=self.process.returncode)

    def __enter__(self) -> "AppServerClient":
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.close()


def write_trace(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(redact(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, path)


__all__ = ["AppServerClient", "extract_ids", "redact", "response_error", "utc_now", "write_trace"]
