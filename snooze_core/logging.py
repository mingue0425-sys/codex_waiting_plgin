from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Dict

from .models import LogInfo, LogLimits, LoggingState


class BoundedLogSet:
    def __init__(self, directory: Path, limits: LogLimits):
        self.directory = directory
        self.limits = limits
        self.directory.mkdir(parents=True, exist_ok=True)
        self._files: Dict[str, object] = {}
        try:
            self._files = {
                "stdout": self._open_log(directory / "stdout.log"),
                "stderr": self._open_log(directory / "stderr.log"),
                "combined": self._open_log(directory / "combined.log"),
            }
        except BaseException:
            for handle in self._files.values():
                try:
                    handle.close()
                except BaseException:
                    pass
            raise
        self._written = {"stdout": 0, "stderr": 0, "combined": 0}
        self._truncated = {"stdout": False, "stderr": False, "combined": False}
        self._preview = bytearray()
        self._preview_lines = 0
        self._preview_line_bytes = 0
        self._preview_truncated = False
        self._sequence = 0
        self._lock = threading.Lock()

    @staticmethod
    def _open_log(path: Path) -> object:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            return os.fdopen(descriptor, "ab")
        except BaseException:
            os.close(descriptor)
            raise

    def _write_bounded(self, stream: str, payload: bytes) -> None:
        remaining = self.limits.max_log_bytes - self._written[stream]
        if remaining <= 0:
            self._truncated[stream] = True
            return
        chunk = payload[:remaining]
        if chunk:
            self._files[stream].write(chunk)
            self._written[stream] += len(chunk)
        if len(chunk) != len(payload):
            self._truncated[stream] = True

    def _add_preview(self, payload: bytes) -> None:
        if not payload:
            return
        for line in payload.splitlines(keepends=True):
            if self._preview_lines >= self.limits.max_preview_lines:
                self._preview_truncated = True
                break
            is_complete_line = line.endswith((b"\n", b"\r"))
            remaining_line_bytes = max(
                0, self.limits.max_single_line_bytes - self._preview_line_bytes
            )
            clipped = line[:remaining_line_bytes]
            if len(clipped) != len(line):
                self._preview_truncated = True
            remaining = self.limits.max_preview_bytes - len(self._preview)
            if remaining <= 0:
                self._preview_truncated = True
                break
            self._preview.extend(clipped[:remaining])
            self._preview_line_bytes += len(clipped)
            if len(clipped) > remaining:
                self._preview_truncated = True
                break
            if is_complete_line:
                self._preview_lines += 1
                self._preview_line_bytes = 0

    def write(self, stream: str, payload: bytes) -> None:
        if stream not in ("stdout", "stderr"):
            raise ValueError(stream)
        with self._lock:
            self._write_bounded(stream, payload)
            self._add_preview(payload)
            self._sequence += 1
            envelope = {
                "seq": self._sequence,
                "monotonic_ns": time.monotonic_ns(),
                "stream": stream,
                "data": payload.decode("utf-8", "replace"),
            }
            encoded = (json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
                "utf-8"
            )
            self._write_bounded("combined", encoded)

    def info(self) -> LogInfo:
        state = LoggingState.OK
        if any(self._truncated.values()) or self._preview_truncated:
            state = LoggingState.TRUNCATED
        return LogInfo(
            stdout_bytes=self._written["stdout"],
            stderr_bytes=self._written["stderr"],
            combined_bytes=self._written["combined"],
            stdout_truncated=self._truncated["stdout"],
            stderr_truncated=self._truncated["stderr"],
            combined_truncated=self._truncated["combined"],
            preview_truncated=self._preview_truncated,
            state=state,
        )

    def preview(self) -> str:
        with self._lock:
            return bytes(self._preview).decode("utf-8", "replace")

    def close(self) -> LogInfo:
        with self._lock:
            for handle in self._files.values():
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
                handle.close()
            return self.info()
