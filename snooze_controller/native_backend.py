from __future__ import annotations

"""Experimental App Server process/spawn backend.

The installed protocol exposes ``process/spawn`` only behind the experimental
initialize capability.  This backend is explicit and opt-in; it is not used by
the default CLI route until sandbox and approval parity are independently
proven.
"""

import base64
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from snooze_core.models import utc_now

from .app_server_process import AppServerProcess, AppServerProcessError


class NativeBackendError(RuntimeError):
    pass


@dataclass
class NativeProcessHandle:
    process_id: str
    command: List[str]
    cwd: str
    started_at: str
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)


class NativeAppServerBackend:
    """Run an explicit argv process through the experimental owned server."""

    experimental_only = True
    requires_security_gate = True

    def __init__(self, process: AppServerProcess):
        if not process.experimental_api:
            raise NativeBackendError("process/spawn requires AppServerProcess(experimental_api=True)")
        self.process = process

    def start(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        process_id: Optional[str] = None,
        timeout_ms: Optional[int] = None,
        stream_output: bool = True,
        output_bytes_cap: int = 1024 * 1024,
    ) -> NativeProcessHandle:
        if not command:
            raise NativeBackendError("native process command cannot be empty")
        handle = NativeProcessHandle(
            process_id=process_id or f"snooze-{uuid.uuid4()}",
            command=[str(item) for item in command],
            cwd=str(Path(cwd).resolve()),
            started_at=utc_now(),
        )
        params: Dict[str, Any] = {
            "command": handle.command,
            "cwd": handle.cwd,
            "processHandle": handle.process_id,
            "streamStdoutStderr": stream_output,
            "streamStdin": False,
            "tty": False,
            "outputBytesCap": output_bytes_cap,
        }
        if timeout_ms is not None:
            if timeout_ms < 0:
                raise NativeBackendError("timeout_ms must be non-negative")
            params["timeoutMs"] = timeout_ms
        response = self.process.request("process/spawn", params, timeout=30.0)
        if response.get("error") is not None:
            raise NativeBackendError(str(response["error"]))
        return handle

    def wait(self, handle: NativeProcessHandle, *, timeout: float = 300.0) -> Dict[str, Any]:
        deadline = __import__("time").monotonic() + timeout
        event_index = 0
        while __import__("time").monotonic() < deadline:
            events = self.process.events
            while event_index < len(events):
                event = events[event_index]
                event_index += 1
                if event.get("kind") != "notification":
                    continue
                params = event.get("params") or {}
                if params.get("processHandle") != handle.process_id:
                    continue
                if event.get("method") == "process/outputDelta":
                    try:
                        chunk = base64.b64decode(str(params.get("deltaBase64", "")), validate=True)
                    except (ValueError, TypeError):
                        chunk = b""
                    if params.get("stream") == "stderr":
                        handle.stderr.extend(chunk)
                    else:
                        handle.stdout.extend(chunk)
                elif event.get("method") == "process/exited":
                    return {
                        "process_handle": handle.process_id,
                        "exit_code": params.get("exitCode"),
                        "stdout": bytes(handle.stdout).decode("utf-8", "replace") or params.get("stdout", ""),
                        "stderr": bytes(handle.stderr).decode("utf-8", "replace") or params.get("stderr", ""),
                        "stdout_cap_reached": params.get("stdoutCapReached", False),
                        "stderr_cap_reached": params.get("stderrCapReached", False),
                    }
            self.process.wait_for_event(lambda item: item.get("kind") == "notification", timeout=0.25, start_index=event_index)
        raise NativeBackendError("native process wait timed out")

    def terminate(self, handle: NativeProcessHandle) -> Dict[str, Any]:
        response = self.process.request("process/kill", {"processHandle": handle.process_id}, timeout=10.0)
        if response.get("error") is not None:
            raise NativeBackendError(str(response["error"]))
        return response.get("result") or {}


__all__ = ["NativeAppServerBackend", "NativeBackendError", "NativeProcessHandle"]
