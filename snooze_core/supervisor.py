from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from .git_fingerprint import changed, fingerprint
from .logging import BoundedLogSet
from .models import (
    Delivery,
    DeliveryState,
    ExecutionState,
    JobSpec,
    LogLimits,
    LoggingState,
    LogInfo,
    Result,
    process_exit_code,
    result_digest,
    utc_now,
)
from .persistence import atomic_write_json
from .process_identity import (
    identity_matches,
    process_exists,
    process_identity,
    signal_process_group,
)
from .store import JobStore, dict_to_identity


def _supervisor_log(directory: Path, event: str, **fields: object) -> None:
    record = {"at": utc_now(), "event": event, **fields}
    try:
        path = directory / "supervisor.log"
        with path.open("a", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


def _identity_dict(identity: object) -> dict[str, object]:
    return {
        "pid": getattr(identity, "pid"),
        "pgid": getattr(identity, "pgid"),
        "start_time": getattr(identity, "start_time"),
        "executable": getattr(identity, "executable"),
        "command_fingerprint": getattr(identity, "command_fingerprint"),
    }


def _write_terminal_failure(store: JobStore, spec: JobSpec, code: str, message: str) -> int:
    directory = store.job_dir(spec.job_id)
    current = store.read_metadata(spec.job_id)
    if current["execution_state"] == ExecutionState.SUBMITTED.value:
        store.update_metadata(
            spec.job_id,
            execution_state=ExecutionState.FAILED,
            result_state="INFRASTRUCTURE_ERROR",
            finish_time=utc_now(),
            error_code=code,
            error_message=message,
        )
    try:
        log_set = BoundedLogSet(directory, LogLimits())
        log_info = log_set.close()
        log_preview = log_set.preview()
    except OSError:
        log_info = LogInfo(state=LoggingState.FAILED)
        log_preview = ""
    payload = Result(
        schema_version=1,
        job_id=spec.job_id,
        spec_sha256=spec.digest(),
        execution_state=ExecutionState.FAILED,
        result_state="INFRASTRUCTURE_ERROR",
        exit_code=None,
        termination_signal=None,
        started_at=None,
        completed_at=utc_now(),
        duration_seconds=None,
        process_identity=None,
        project_fingerprint_start=None,
        project_fingerprint_end=None,
        source_state_changed=None,
        logging=log_info,
        log_preview=log_preview,
        completion_event_id=str(uuid.uuid4()),
        error_code=code,
        error_message=message,
    )
    result_payload = payload.as_dict()
    result_payload["result_sha256"] = result_digest(result_payload)
    payload.result_sha256 = result_payload["result_sha256"]
    atomic_write_json(directory / "result.json", payload.as_dict())
    store.write_delivery(
        spec.job_id,
        Delivery(
            schema_version=1,
            job_id=spec.job_id,
            completion_event_id=payload.completion_event_id,
            result_sha256=payload.result_sha256,
            state=DeliveryState.PENDING,
        ),
    )
    return 125


def _load_limits(value: Optional[str]) -> LogLimits:
    if not value:
        return LogLimits()
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("log limits must be a JSON object")
    return LogLimits(
        max_log_bytes=int(parsed.get("max_log_bytes", LogLimits.max_log_bytes)),
        max_preview_bytes=int(parsed.get("max_preview_bytes", LogLimits.max_preview_bytes)),
        max_preview_lines=int(parsed.get("max_preview_lines", LogLimits.max_preview_lines)),
        max_single_line_bytes=int(
            parsed.get("max_single_line_bytes", LogLimits.max_single_line_bytes)
        ),
    )


def run_job(store_root: Path, job_id: str, limits: LogLimits) -> int:
    store = JobStore(store_root)
    directory = store.job_dir(job_id)
    try:
        spec = store.read_spec(job_id)
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        _supervisor_log(directory, "spec_read_failed", error_type=type(exc).__name__, error=str(exc))
        return 125
    lock_path = directory / "supervisor.lock"
    with lock_path.open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            _supervisor_log(directory, "duplicate_supervisor_refused")
            return 125

        spec_path = directory / "spec.json"
        on_disk_spec = store.read_spec(job_id)
        if on_disk_spec.digest() != spec.digest():
            return _write_terminal_failure(
                store,
                spec,
                "JOB_SPEC_TAMPERED",
                "canonical JobSpec changed before execution",
            )
        metadata = store.read_metadata(job_id)
        if metadata.get("spec_sha256") != spec.digest():
            return _write_terminal_failure(
                store,
                spec,
                "JOB_SPEC_TAMPERED",
                "metadata JobSpec digest does not match spec.json",
            )
        if any(
            metadata.get(key) != getattr(spec, key)
            for key in ("job_id", "command", "cwd", "shell")
        ):
            return _write_terminal_failure(
                store,
                spec,
                "JOB_SPEC_TAMPERED",
                "immutable JobSpec fields changed in metadata.json",
            )
        if spec_path.stat().st_mode & 0o022:
            return _write_terminal_failure(
                store,
                spec,
                "JOB_SPEC_UNSAFE_PERMISSIONS",
                "spec.json is writable by group or others",
            )

        started_at: Optional[str] = None
        process_id = None
        identity = None
        project_start = None
        try:
            log_set = BoundedLogSet(directory, limits)
        except OSError as exc:
            return _write_terminal_failure(
                store,
                spec,
                "LOG_INIT_FAILED",
                f"could not initialize log files: {exc}",
            )
        reader_errors: list[str] = []

        try:
            if not Path(spec.cwd).is_dir():
                return _write_terminal_failure(
                    store, spec, "CWD_NOT_FOUND", f"working directory does not exist: {spec.cwd}"
                )
            shell = Path(spec.shell)
            if not shell.is_file() or not os.access(shell, os.X_OK):
                return _write_terminal_failure(
                    store, spec, "SHELL_NOT_EXECUTABLE", f"shell is not executable: {spec.shell}"
                )
            shell_name = shell.name
            if shell_name not in {"zsh", "bash"}:
                return _write_terminal_failure(
                    store,
                    spec,
                    "SHELL_UNSUPPORTED",
                    f"only zsh and bash are supported in v0.1: {spec.shell}",
                )

            project_start = fingerprint(Path(spec.cwd))
            started_at = utc_now()
            supervisor_pid = os.getpid()
            supervisor_identity = process_identity(supervisor_pid)
            store.update_metadata(
                job_id,
                execution_state=ExecutionState.STARTING,
                supervisor_pid=supervisor_pid,
                supervisor_identity=_identity_dict(supervisor_identity),
                start_time=started_at,
                project_fingerprint_start=project_start,
                error_code=None,
                error_message=None,
            )
            _supervisor_log(directory, "starting", supervisor_pid=supervisor_pid)

            process = subprocess.Popen(
                [str(shell), "-c", spec.command],
                cwd=spec.cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                close_fds=True,
                env=os.environ.copy(),
            )
            process_id = process.pid
            identity = process_identity(process.pid)
            store.update_metadata(
                job_id,
                execution_state=ExecutionState.RUNNING,
                pid=process.pid,
                process_group=identity.pgid,
                process_identity={
                    "pid": identity.pid,
                    "pgid": identity.pgid,
                    "start_time": identity.start_time,
                    "executable": identity.executable,
                    "command_fingerprint": identity.command_fingerprint,
                },
            )
            _supervisor_log(
                directory,
                "running",
                pid=process.pid,
                process_group=identity.pgid,
                command_fingerprint=identity.command_fingerprint,
            )

            def copy_stream(name: str, stream: object) -> None:
                try:
                    while True:
                        read1 = getattr(stream, "read1", None)
                        chunk = read1(64 * 1024) if read1 is not None else stream.read(64 * 1024)
                        if not chunk:
                            break
                        try:
                            log_set.write(name, chunk)
                        except OSError as exc:
                            # Continue reading so a verbose child cannot block
                            # forever after the log destination fills or fails.
                            reader_errors.append(f"{name}:write:{type(exc).__name__}:{exc}")
                except BaseException as exc:
                    reader_errors.append(f"{name}:{type(exc).__name__}:{exc}")

            stdout_thread = threading.Thread(
                target=copy_stream, args=("stdout", process.stdout), daemon=True
            )
            stderr_thread = threading.Thread(
                target=copy_stream, args=("stderr", process.stderr), daemon=True
            )
            stdout_thread.start()
            stderr_thread.start()
            returncode = process.wait()
            stdout_thread.join()
            stderr_thread.join()
            log_info = log_set.close()
            if reader_errors:
                log_info.state = LoggingState.FAILED
                _supervisor_log(directory, "logging_failed", errors=reader_errors)
            else:
                _supervisor_log(directory, "logging_finished", info=log_info.as_dict())

            finished_at = utc_now()
            project_end = fingerprint(Path(spec.cwd))
            stale = changed(project_start, project_end)
            exit_code, termination_signal = process_exit_code(returncode)
            latest_metadata = store.read_metadata(job_id)
            cancellation_requested = latest_metadata.get("cancellation_requested_at") is not None
            if cancellation_requested:
                execution_state = ExecutionState.CANCELLED
                result_state = "CANCELLED"
            elif returncode == 0:
                execution_state = (
                    ExecutionState.COMPLETED_STALE if stale else ExecutionState.COMPLETED
                )
                result_state = "SUCCESS"
            elif termination_signal is not None:
                execution_state = ExecutionState.CANCELLED if termination_signal == signal.SIGTERM else ExecutionState.FAILED
                result_state = "SIGNAL"
            else:
                execution_state = ExecutionState.FAILED
                result_state = "NONZERO_EXIT"
            duration = None
            if started_at:
                try:
                    from datetime import datetime

                    duration = (
                        datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
                        - datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                    ).total_seconds()
                except ValueError:
                    duration = None
            result = Result(
                schema_version=1,
                job_id=job_id,
                spec_sha256=spec.digest(),
                execution_state=execution_state,
                result_state=result_state,
                exit_code=exit_code,
                termination_signal=termination_signal,
                started_at=started_at,
                completed_at=finished_at,
                duration_seconds=duration,
                process_identity=identity,
                project_fingerprint_start=project_start,
                project_fingerprint_end=project_end,
                source_state_changed=stale,
                logging=log_info,
                log_preview=log_set.preview(),
                completion_event_id=str(uuid.uuid4()),
            )
            payload_without_hash = result.as_dict()
            result_hash = result_digest(payload_without_hash)
            result.result_sha256 = result_hash
            store.write_result(job_id, result)
            store.update_metadata(
                job_id,
                execution_state=execution_state,
                result_state=result_state,
                finish_time=finished_at,
                exit_code=exit_code,
                termination_signal=termination_signal,
                project_fingerprint_end=project_end,
                stale=stale,
                logging=log_info.as_dict(),
                error_code=None,
                error_message=None,
            )
            store.write_delivery(
                job_id,
                Delivery(
                    schema_version=1,
                    job_id=job_id,
                    completion_event_id=result.completion_event_id,
                    result_sha256=result_hash,
                    state=DeliveryState.PENDING,
                ),
            )
            _supervisor_log(
                directory,
                "completed",
                execution_state=execution_state.value,
                result_state=result_state,
                exit_code=exit_code,
                termination_signal=termination_signal,
                stale=stale,
                result_sha256=result_hash,
            )
            if exit_code is not None:
                return exit_code
            return 128 + (termination_signal or 1)
        except BaseException as exc:
            _supervisor_log(
                directory,
                "supervisor_exception",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            try:
                if process_id is not None and identity is not None and identity_matches(identity):
                    signal_process_group(identity, signal.SIGTERM)
            except (OSError, RuntimeError):
                pass
            try:
                log_info = log_set.close()
            except BaseException:
                log_info = None
            latest = store.read_metadata(job_id)
            if latest.get("execution_state") in {
                ExecutionState.STARTING.value,
                ExecutionState.RUNNING.value,
            }:
                try:
                    store.update_metadata(
                        job_id,
                        execution_state=ExecutionState.LOST,
                        result_state="SUPERVISOR_EXCEPTION",
                        finish_time=utc_now(),
                        error_code="SUPERVISOR_EXCEPTION",
                        error_message=str(exc),
                        logging=log_info.as_dict() if log_info else latest.get("logging"),
                    )
                except BaseException:
                    pass
            return 125


def cancel_job(store_root: Path, job_id: str, grace_seconds: float) -> int:
    store = JobStore(store_root)
    metadata = store.read_metadata(job_id)
    terminal = {
        state.value for state in (ExecutionState.COMPLETED, ExecutionState.COMPLETED_STALE, ExecutionState.FAILED, ExecutionState.CANCELLED, ExecutionState.LOST, ExecutionState.ORPHANED)
    }
    if metadata.get("execution_state") in terminal:
        return 0
    store.update_metadata(job_id, cancellation_requested_at=utc_now())
    identity = dict_to_identity(metadata.get("process_identity"))
    if identity is None:
        return 1
    try:
        signal_process_group(identity, signal.SIGTERM)
    except (OSError, RuntimeError):
        return 1
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not process_exists(identity.pid):
            return 0
        time.sleep(min(0.25, max(0.01, deadline - time.monotonic())))
    try:
        if identity_matches(identity, include_command=False):
            signal_process_group(identity, signal.SIGKILL)
    except (OSError, RuntimeError):
        return 1
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Codex Snooze internal supervisor")
    parser.add_argument("--run", metavar="JOB_ID")
    parser.add_argument("--store", required=True)
    parser.add_argument("--cancel", metavar="JOB_ID")
    parser.add_argument("--grace-seconds", type=float, default=5.0)
    parser.add_argument("--log-limits")
    args = parser.parse_args(argv)
    if bool(args.run) == bool(args.cancel):
        parser.error("choose exactly one of --run or --cancel")
    if args.cancel:
        return cancel_job(Path(args.store), args.cancel, args.grace_seconds)
    try:
        limits = _load_limits(args.log_limits)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"invalid log limits: {exc}", file=sys.stderr)
        return 2
    return run_job(Path(args.store), args.run, limits)


if __name__ == "__main__":
    raise SystemExit(main())
