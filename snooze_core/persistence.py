from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

from .models import canonical_json


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _fault_injection(point: str, path: Path) -> None:
    """Crash at a named persistence boundary when explicitly requested.

    This is intentionally opt-in and process terminating so tests can model an
    abrupt supervisor loss without pretending that an exception is equivalent
    to a power failure.  The optional target matches the basename, which keeps
    the setting useful when the same process writes several files.
    """
    configured = os.environ.get("SNOOZE_FAULT_POINT")
    if configured not in {point, "*"}:
        return
    target = os.environ.get("SNOOZE_FAULT_TARGET")
    if target and target not in {path.name, str(path)}:
        return
    try:
        exit_code = int(os.environ.get("SNOOZE_FAULT_EXIT_CODE", "75"))
    except ValueError:
        exit_code = 75
    os._exit(max(1, min(255, exit_code)))


def atomic_write_bytes(path: Path, payload: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _fault_injection("after_temp_fsync", path)
        _fault_injection("before_rename", path)
        os.replace(temporary_path, path)
        _fault_injection("after_rename", path)
        _fsync_directory(path.parent)
        _fault_injection("after_directory_fsync", path)
    except BaseException:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, value: Dict[str, Any], mode: int = 0o600) -> None:
    atomic_write_bytes(
        path,
        canonical_json(value) + b"\n",
        mode=mode,
    )


def write_text(path: Path, value: str, mode: int = 0o600) -> None:
    atomic_write_bytes(path, value.encode("utf-8"), mode=mode)
