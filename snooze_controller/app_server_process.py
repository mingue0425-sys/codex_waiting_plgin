from __future__ import annotations

"""Owned Codex App Server process and newline JSON-RPC connection.

This module is intentionally independent from the v0.1 supervisor.  It owns
one ``codex app-server --stdio`` child for the lifetime of a controller and
keeps the protocol stream, diagnostics, lifecycle state and server requests
separate.  A server request is never approved implicitly; the default policy
responds with an explicit rejection until a caller supplies a handler.
"""

import json
import os
import queue
import subprocess
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, List, Optional, Sequence

from tools.app_server_probe import redact, utc_now, write_trace


class AppServerLifecycle(str, Enum):
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    DEGRADED = "DEGRADED"
    CRASHED = "CRASHED"
    RESTARTING = "RESTARTING"
    FAILED = "FAILED"


class AppServerProcessError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServerRequest:
    request_id: Any
    method: str
    params: Any
    received_at: str


ServerRequestHandler = Callable[[ServerRequest], Optional[Dict[str, Any]]]


class AppServerProcess:
    """Own one persistent App Server subprocess and its JSON-RPC stream."""

    def __init__(
        self,
        command: Sequence[str] = ("codex", "app-server", "--stdio"),
        *,
        cwd: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
        client_name: str = "codex_snooze",
        client_title: str = "Codex Snooze",
        client_version: str = "0.3.0",
        experimental_api: bool = False,
        request_handler: Optional[ServerRequestHandler] = None,
        max_events: int = 4000,
        max_stderr: int = 400,
    ) -> None:
        if not command:
            raise ValueError("App Server command cannot be empty")
        if max_events <= 0 or max_stderr <= 0:
            raise ValueError("event bounds must be positive")
        self.command = list(command)
        self.cwd = Path(cwd).resolve() if cwd is not None else None
        self.env = dict(env) if env is not None else None
        self.client_name = client_name
        self.client_title = client_title
        self.client_version = client_version
        self.experimental_api = bool(experimental_api)
        self.request_handler = request_handler
        self.max_events = max_events
        self.max_stderr = max_stderr

        self._state = AppServerLifecycle.STOPPED
        self._state_lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._request_counter = 0
        self._pending: Dict[int, queue.Queue[Dict[str, Any]]] = {}
        self._pending_lock = threading.Lock()
        self._messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._notifications: Deque[Dict[str, Any]] = deque(maxlen=max_events)
        self._server_requests: Deque[ServerRequest] = deque(maxlen=max_events)
        self._events: Deque[Dict[str, Any]] = deque(maxlen=max_events)
        self._stderr: Deque[str] = deque(maxlen=max_stderr)
        self._process: Optional[subprocess.Popen[bytes]] = None
        self._stdout_thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._notify_condition = threading.Condition()
        self._closed_by_owner = False
        self._stop_lock = threading.Lock()
        self._restart_count = 0
        self._instance_id: Optional[str] = None
        self._started_at: Optional[str] = None
        self._last_exit: Optional[Dict[str, Any]] = None

    @property
    def state(self) -> AppServerLifecycle:
        with self._state_lock:
            return self._state

    @property
    def process(self) -> Optional[subprocess.Popen[bytes]]:
        return self._process

    @property
    def pid(self) -> Optional[int]:
        process = self._process
        return process.pid if process is not None else None

    @property
    def instance_id(self) -> Optional[str]:
        return self._instance_id

    @property
    def events(self) -> List[Dict[str, Any]]:
        return list(self._events)

    @property
    def notifications(self) -> List[Dict[str, Any]]:
        return list(self._notifications)

    @property
    def server_requests(self) -> List[ServerRequest]:
        return list(self._server_requests)

    @property
    def stderr_tail(self) -> List[str]:
        return list(self._stderr)

    def _set_state(self, state: AppServerLifecycle, *, reason: Optional[str] = None) -> None:
        with self._state_lock:
            self._state = state
        self._record("lifecycle", state=state.value, reason=reason)

    def _record(self, kind: str, **fields: Any) -> None:
        event = {
            "sequence": len(self._events) + 1,
            "timestamp": utc_now(),
            "monotonic_ns": time.monotonic_ns(),
            "kind": kind,
            **redact(fields),
        }
        self._events.append(event)
        with self._notify_condition:
            self._notify_condition.notify_all()

    def _spawn(self) -> None:
        try:
            process = subprocess.Popen(
                self.command,
                cwd=str(self.cwd) if self.cwd is not None else None,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                env=self.env,
            )
        except OSError as exc:
            self._set_state(AppServerLifecycle.FAILED, reason=f"spawn: {exc}")
            raise AppServerProcessError(f"could not start App Server: {exc}") from exc
        self._process = process
        self._instance_id = str(uuid.uuid4())
        self._started_at = utc_now()
        self._closed_by_owner = False
        self._record("process_started", pid=process.pid, instance_id=self._instance_id, command=self.command)
        assert process.stdout is not None and process.stderr is not None
        self._stdout_thread = threading.Thread(
            target=self._read_stdout,
            name=f"app-server-stdout-{process.pid}",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            name=f"app-server-stderr-{process.pid}",
            daemon=True,
        )
        self._monitor_thread = threading.Thread(
            target=self._monitor_process,
            name=f"app-server-monitor-{process.pid}",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()
        self._monitor_thread.start()

    def _monitor_process(self) -> None:
        process = self._process
        if process is None:
            return
        while process.poll() is None:
            if self._closed_by_owner:
                return
            time.sleep(0.05)
        if not self._closed_by_owner:
            self._mark_crashed("process exited")

    def start(self, *, timeout: float = 20.0) -> Dict[str, Any]:
        """Start and initialize the owned process before returning."""
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        with self._state_lock:
            if self._state not in {
                AppServerLifecycle.STOPPED,
                AppServerLifecycle.CRASHED,
                AppServerLifecycle.RESTARTING,
            }:
                raise AppServerProcessError(f"cannot start from {self._state.value}")
            self._state = AppServerLifecycle.STARTING
        self._record("lifecycle", state=AppServerLifecycle.STARTING.value)
        self._spawn()
        self._set_state(AppServerLifecycle.INITIALIZING)
        try:
            initialize_params: Dict[str, Any] = {
                "clientInfo": {
                    "name": self.client_name,
                    "title": self.client_title,
                    "version": self.client_version,
                }
            }
            if self.experimental_api:
                initialize_params["capabilities"] = {"experimentalApi": True}
            response = self.request(
                "initialize",
                initialize_params,
                timeout=timeout,
                allow_before_ready=True,
            )
            if response.get("error") is not None or not isinstance(response.get("result"), dict):
                self._set_state(AppServerLifecycle.FAILED, reason="initialize rejected")
                self.stop()
                raise AppServerProcessError(f"initialize failed: {redact(response)}")
        except BaseException:
            if self.state not in {AppServerLifecycle.FAILED, AppServerLifecycle.STOPPED}:
                self._set_state(AppServerLifecycle.FAILED, reason="initialize exception")
            self.stop()
            raise
        self._set_state(AppServerLifecycle.READY)
        return response

    def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        fd = process.stdout.fileno()
        buffer = b""
        try:
            while True:
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    self._handle_stdout_line(raw)
            if buffer.strip():
                self._record("stdout_partial_line", line=buffer.decode("utf-8", "replace"))
        except (OSError, ValueError) as exc:
            if not self._closed_by_owner:
                self._record("stdout_error", error=f"{type(exc).__name__}: {exc}")
        finally:
            self._record("stdout_closed")
            if not self._closed_by_owner:
                self._mark_crashed("stdout closed")

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        fd = process.stderr.fileno()
        buffer = b""
        try:
            while True:
                chunk = os.read(fd, 16384)
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    self._handle_stderr_line(raw)
            if buffer:
                self._handle_stderr_line(buffer)
        except (OSError, ValueError) as exc:
            self._record("stderr_error", error=f"{type(exc).__name__}: {exc}")
        finally:
            self._record("stderr_closed")

    def _handle_stderr_line(self, raw: bytes) -> None:
        line = raw.decode("utf-8", "replace")
        self._stderr.append(str(redact(line)))
        self._record("stderr", line=line)

    def _handle_stdout_line(self, raw: bytes) -> None:
        line = raw.decode("utf-8", "replace")
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            self._record("stdout_parse_error", line=line, error=str(exc))
            return
        if not isinstance(message, dict):
            self._record("json_message", value=message)
            return
        if "id" in message and ("result" in message or "error" in message):
            request_id = message.get("id")
            self._record("response", request_id=request_id, result=message.get("result"), error=message.get("error"))
            with self._pending_lock:
                waiter = self._pending.get(request_id)
            if waiter is not None:
                waiter.put(message)
            else:
                self._messages.put(("response", message))
            return
        if "id" in message and "method" in message:
            request = ServerRequest(
                request_id=message.get("id"),
                method=str(message.get("method")),
                params=message.get("params"),
                received_at=utc_now(),
            )
            self._server_requests.append(request)
            self._record(
                "server_request",
                request_id=request.request_id,
                method=request.method,
                params=request.params,
            )
            response: Optional[Dict[str, Any]] = None
            if self.request_handler is not None:
                try:
                    response = self.request_handler(request)
                except Exception as exc:  # handler failures must reject, never allow
                    self._record("server_request_handler_error", error=f"{type(exc).__name__}: {exc}")
            if response is None:
                response = {
                    "id": request.request_id,
                    "error": {
                        "code": -32001,
                        "message": "Codex Snooze requires explicit handling of this server request",
                    },
                }
            else:
                response = dict(response)
                response.setdefault("id", request.request_id)
            self._send_raw(response, kind="server_response")
            self._messages.put(("server_request", request))
            return
        if "method" in message:
            notification = {
                "method": message.get("method"),
                "params": message.get("params"),
                "received_at": utc_now(),
            }
            self._notifications.append(notification)
            self._record("notification", method=notification["method"], params=notification["params"])
            self._messages.put(("notification", notification))
            with self._notify_condition:
                self._notify_condition.notify_all()
            return
        self._record("json_message", value=message)

    def _send_raw(self, message: Dict[str, Any], *, kind: str) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise AppServerProcessError("App Server stdin is unavailable")
        encoded = (json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            with self._write_lock:
                process.stdin.write(encoded)
                process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._record("write_error", operation=kind, error=f"{type(exc).__name__}: {exc}")
            self._mark_crashed("write failed")
            raise AppServerProcessError(f"App Server write failed: {exc}") from exc
        self._record(
            kind,
            request_id=message.get("id"),
            method=message.get("method"),
            params=message.get("params"),
            result=message.get("result"),
            error=message.get("error"),
        )

    def request(
        self,
        method: str,
        params: Any,
        *,
        timeout: float = 30.0,
        allow_before_ready: bool = False,
    ) -> Dict[str, Any]:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if self.state not in {AppServerLifecycle.READY, AppServerLifecycle.INITIALIZING} and not allow_before_ready:
            raise AppServerProcessError(f"App Server is not ready: {self.state.value}")
        with self._pending_lock:
            self._request_counter += 1
            request_id = self._request_counter
            waiter: queue.Queue[Dict[str, Any]] = queue.Queue(maxsize=1)
            self._pending[request_id] = waiter
        try:
            self._send_raw({"id": request_id, "method": method, "params": params}, kind="request")
            try:
                return waiter.get(timeout=timeout)
            except queue.Empty as exc:
                raise AppServerProcessError(f"timed out waiting for {method} response {request_id}") from exc
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def notify(self, method: str, params: Any) -> None:
        if self.state not in {AppServerLifecycle.READY, AppServerLifecycle.INITIALIZING}:
            raise AppServerProcessError(f"App Server is not ready: {self.state.value}")
        self._send_raw({"method": method, "params": params}, kind="notification_sent")

    def respond(self, request_id: Any, *, result: Any = None, error: Optional[Dict[str, Any]] = None) -> None:
        if error is not None and result is not None:
            raise ValueError("JSON-RPC response cannot contain both result and error")
        message: Dict[str, Any] = {"id": request_id}
        message["error" if error is not None else "result"] = error if error is not None else result
        self._send_raw(message, kind="server_response")

    def wait_for_notification(
        self,
        predicate: Callable[[Dict[str, Any]], bool],
        *,
        timeout: float = 30.0,
        start_index: int = 0,
    ) -> Optional[Dict[str, Any]]:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        deadline = time.monotonic() + timeout
        index = max(0, start_index)
        while time.monotonic() < deadline:
            values = list(self._notifications)
            while index < len(values):
                candidate = values[index]
                index += 1
                if predicate(candidate):
                    return candidate
            with self._notify_condition:
                self._notify_condition.wait(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
        values = list(self._notifications)
        while index < len(values):
            candidate = values[index]
            index += 1
            if predicate(candidate):
                return candidate
        return None

    def wait_for_event(
        self,
        predicate: Callable[[Dict[str, Any]], bool],
        *,
        timeout: float = 30.0,
        start_index: int = 0,
    ) -> Optional[Dict[str, Any]]:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        deadline = time.monotonic() + timeout
        index = max(0, start_index)
        while time.monotonic() < deadline:
            values = list(self._events)
            while index < len(values):
                candidate = values[index]
                index += 1
                if predicate(candidate):
                    return candidate
            with self._notify_condition:
                self._notify_condition.wait(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
        return None

    def _mark_crashed(self, reason: str) -> None:
        if self._closed_by_owner:
            return
        process = self._process
        returncode = process.poll() if process is not None else None
        self._last_exit = {"returncode": returncode, "reason": reason, "at": utc_now()}
        if self.state not in {AppServerLifecycle.STOPPED, AppServerLifecycle.FAILED, AppServerLifecycle.CRASHED}:
            self._set_state(AppServerLifecycle.CRASHED, reason=reason)
        with self._pending_lock:
            pending = list(self._pending.items())
        for request_id, waiter in pending:
            try:
                waiter.put_nowait(
                    {
                        "id": request_id,
                        "error": {"code": -32098, "message": "App Server process exited"},
                    }
                )
            except queue.Full:
                pass
        with self._notify_condition:
            self._notify_condition.notify_all()

    def stop(self, *, timeout: float = 5.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        with self._stop_lock:
            process = self._process
            if process is None:
                self._set_state(AppServerLifecycle.STOPPED)
                return
            self._closed_by_owner = True
            self._record("stop_requested", pid=process.pid)
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._record("terminate_requested", pid=process.pid)
                process.terminate()
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self._record("kill_requested", pid=process.pid)
                    process.kill()
                    process.wait(timeout=timeout)
            for thread in (self._stdout_thread, self._stderr_thread, self._monitor_thread):
                if thread is not None and thread.is_alive():
                    thread.join(timeout=min(1.0, timeout))
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
            self._last_exit = {"returncode": process.returncode, "at": utc_now(), "reason": "owner_stop"}
            self._record("process_exit", returncode=process.returncode)
            self._process = None
            self._stdout_thread = None
            self._stderr_thread = None
            self._monitor_thread = None
            self._set_state(AppServerLifecycle.STOPPED)

    def restart(self, *, timeout: float = 20.0, max_restarts: int = 1) -> Dict[str, Any]:
        if max_restarts < 1:
            raise ValueError("max_restarts must be positive")
        if self._restart_count >= max_restarts:
            raise AppServerProcessError("restart limit exhausted")
        self._restart_count += 1
        self._set_state(AppServerLifecycle.RESTARTING, reason=f"attempt {self._restart_count}")
        self.stop()
        self._set_state(AppServerLifecycle.RESTARTING)
        return self.start(timeout=timeout)

    def snapshot(self) -> Dict[str, Any]:
        process = self._process
        return {
            "state": self.state.value,
            "command": self.command,
            "cwd": str(self.cwd) if self.cwd is not None else None,
            "pid": process.pid if process is not None else None,
            "returncode": process.poll() if process is not None else (self._last_exit or {}).get("returncode"),
            "instance_id": self._instance_id,
            "started_at": self._started_at,
            "restart_count": self._restart_count,
            "experimental_api": self.experimental_api,
            "last_exit": self._last_exit,
            "events": self.events,
            "notifications": self.notifications,
            "server_requests": [
                {"id": item.request_id, "method": item.method, "params": item.params, "received_at": item.received_at}
                for item in self.server_requests
            ],
            "stderr_tail": self.stderr_tail,
        }

    def write_trace(self, path: Path) -> None:
        write_trace(path, self.snapshot())

    def __enter__(self) -> "AppServerProcess":
        self.start()
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        self.stop()


__all__ = [
    "AppServerLifecycle",
    "AppServerProcess",
    "AppServerProcessError",
    "ServerRequest",
]
