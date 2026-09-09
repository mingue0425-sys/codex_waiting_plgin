from __future__ import annotations

import hashlib
import os
import signal
import subprocess
from pathlib import Path
from typing import Optional

from .models import ProcessIdentity


def _ps(pid: int, field: str) -> Optional[str]:
    try:
        completed = subprocess.run(
            ["ps", "-o", f"{field}=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def process_identity(pid: int) -> ProcessIdentity:
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = None
    executable = _ps(pid, "comm")
    command = _ps(pid, "command")
    start_time = _ps(pid, "lstart")
    command_fingerprint = (
        hashlib.sha256(command.encode("utf-8", "replace")).hexdigest()
        if command
        else None
    )
    return ProcessIdentity(
        pid=pid,
        pgid=pgid,
        start_time=start_time,
        executable=executable,
        command_fingerprint=command_fingerprint,
    )


def identity_matches(expected: ProcessIdentity, *, include_command: bool = True) -> bool:
    current = process_identity(expected.pid)
    if current.pid != expected.pid:
        return False
    if expected.pgid is not None and current.pgid != expected.pgid:
        return False
    if expected.start_time is not None and current.start_time != expected.start_time:
        return False
    if include_command and (
        expected.command_fingerprint is not None
        and current.command_fingerprint != expected.command_fingerprint
    ):
        return False
    return True


def process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def signal_process_group(expected: ProcessIdentity, sig: signal.Signals) -> None:
    # A shell can legitimately exec its final command in place, changing
    # comm/command while retaining the same PID, PGID and start time. Those
    # stable identity fields protect against PID reuse; command is retained as
    # evidence rather than making cancellation race with shell exec behavior.
    if not identity_matches(expected, include_command=False):
        raise RuntimeError("process identity changed; refusing to signal")
    if expected.pgid is None:
        os.kill(expected.pid, sig)
        return
    os.killpg(expected.pgid, sig)
