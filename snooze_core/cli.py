from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from .models import JobSpec
from .recovery import recover_store
from .store import JobStore
from .supervisor import cancel_job
from snooze_controller.controller import DeliveryController, DeliveryControllerError


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_store() -> Path:
    configured = os.environ.get("CODEX_SNOOZE_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex-snooze"


def select_shell(requested: Optional[str]) -> Path:
    candidate = requested or os.environ.get("SHELL")
    if candidate:
        path = Path(candidate).expanduser()
        if path.name in {"zsh", "bash"} and path.is_file() and os.access(path, os.X_OK):
            return path.resolve()
    for name in ("zsh", "bash"):
        found = shutil.which(name)
        if found:
            return Path(found).resolve()
    raise RuntimeError("no executable zsh or bash found; pass --shell explicitly")


def command_from_args(command: Optional[str], remainder: list[str]) -> str:
    if command is not None:
        if not command:
            raise ValueError("--command cannot be empty")
        return command
    remainder = list(remainder)
    if remainder and remainder[0] == "--":
        remainder = remainder[1:]
    if not remainder:
        raise ValueError("provide --command or a command after --")
    # The remainder is already tokenized by the caller's shell. Reconstruct a
    # valid argv command for the common explicit form. Exact shell source,
    # including operators and quoting, must use --command.
    operators = {"&&", "||", "|", ">", ">>", "2>&1", "<", ";"}
    pieces = [token if token in operators else shlex.quote(token) for token in remainder]
    return " ".join(pieces)


def _spawn_supervisor(store: JobStore, job_id: str, log_limits: Optional[dict]) -> subprocess.Popen[bytes]:
    package_root = _project_root()
    command = [
        sys.executable,
        "-m",
        "snooze_core.supervisor",
        "--run",
        job_id,
        "--store",
        str(store.root),
    ]
    if log_limits is not None:
        command.extend(["--log-limits", json.dumps(log_limits, separators=(",", ":"))])
    environment = os.environ.copy()
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        str(package_root)
        if not existing_pythonpath
        else os.pathsep.join((str(package_root), existing_pythonpath))
    )
    return subprocess.Popen(
        command,
        cwd=package_root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        env=environment,
    )


def submit(args: argparse.Namespace) -> int:
    try:
        command = command_from_args(args.command, args.remainder)
        cwd = Path(args.cwd or os.getcwd()).expanduser().resolve()
        shell = select_shell(args.shell)
        if not cwd.is_dir():
            raise ValueError(f"cwd is not a directory: {cwd}")
        if args.handoff_after is not None and args.handoff_after < 0:
            raise ValueError("--handoff-after must be non-negative")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"submit failed: {exc}", file=sys.stderr)
        return 2
    store = JobStore(Path(args.store))
    spec = JobSpec.create(command=command, cwd=cwd, shell=str(shell))
    store.create(spec)
    supervisor = _spawn_supervisor(store, spec.job_id, args.log_limits)
    if args.detach:
        print(json.dumps({"job_id": spec.job_id, "supervisor_pid": supervisor.pid}))
        return 0
    if args.handoff_after is not None:
        deadline = time.monotonic() + args.handoff_after
        while supervisor.poll() is None and time.monotonic() < deadline:
            time.sleep(min(0.2, max(0.01, deadline - time.monotonic())))
        if supervisor.poll() is None:
            print(
                json.dumps(
                    {
                        "job_id": spec.job_id,
                        "supervisor_pid": supervisor.pid,
                        "handoff": True,
                        "handoff_after_seconds": args.handoff_after,
                    },
                    ensure_ascii=False,
                )
            )
            return 0
    returncode = supervisor.wait()
    result = store.read_result(spec.job_id)
    if result is None:
        print(
            json.dumps(
                {
                    "job_id": spec.job_id,
                    "execution_state": "LOST",
                    "supervisor_returncode": returncode,
                }
            ),
            file=sys.stderr,
        )
        return 125
    print(
        json.dumps(
            {
                "job_id": spec.job_id,
                "execution_state": result.get("execution_state"),
                "result_state": result.get("result_state"),
                "exit_code": result.get("exit_code"),
                "termination_signal": result.get("termination_signal"),
                "completion_event_id": result.get("completion_event_id"),
            },
            ensure_ascii=False,
        )
    )
    if result.get("exit_code") is not None:
        return int(result["exit_code"])
    signal_number = result.get("termination_signal")
    return 128 + int(signal_number) if signal_number is not None else 125


def status(args: argparse.Namespace) -> int:
    store = JobStore(Path(args.store))
    try:
        metadata = store.read_metadata(args.job_id)
    except (OSError, ValueError, KeyError) as exc:
        print(f"status failed: {exc}", file=sys.stderr)
        return 2
    result = store.read_result(args.job_id)
    delivery = store.read_delivery(args.job_id)
    print(json.dumps({"metadata": metadata, "result": result, "delivery": delivery}, ensure_ascii=False, indent=2))
    return 0


def result(args: argparse.Namespace) -> int:
    store = JobStore(Path(args.store))
    try:
        value = store.read_result(args.job_id)
    except (OSError, ValueError) as exc:
        print(f"result failed: {exc}", file=sys.stderr)
        return 2
    if value is None:
        print("result is not available", file=sys.stderr)
        return 1
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def cancel(args: argparse.Namespace) -> int:
    try:
        return cancel_job(Path(args.store), args.job_id, args.grace_seconds)
    except (OSError, ValueError, KeyError) as exc:
        print(f"cancel failed: {exc}", file=sys.stderr)
        return 2


def list_jobs(args: argparse.Namespace) -> int:
    store = JobStore(Path(args.store))
    for job_id in store.list_job_ids():
        metadata = store.read_metadata(job_id)
        print(
            json.dumps(
                {
                    "job_id": job_id,
                    "execution_state": metadata.get("execution_state"),
                    "result_state": metadata.get("result_state"),
                    "command": metadata.get("command"),
                },
                ensure_ascii=False,
            )
        )
    return 0


def recover(args: argparse.Namespace) -> int:
    try:
        reports = recover_store(Path(args.store), args.job_id)
    except (OSError, ValueError, KeyError) as exc:
        print(f"recover failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 1 if any(report.get("action") == "recovery_error" for report in reports) else 0


def deliver(args: argparse.Namespace) -> int:
    try:
        controller = DeliveryController(JobStore(Path(args.store)))
        value = controller.deliver_manual(args.job_id, allow_duplicate=args.allow_duplicate)
    except (OSError, ValueError, KeyError, DeliveryControllerError) as exc:
        print(f"deliver failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def acknowledge(args: argparse.Namespace) -> int:
    try:
        controller = DeliveryController(JobStore(Path(args.store)))
        value = controller.acknowledge(args.job_id, event_id=args.event_id)
    except (OSError, ValueError, KeyError, DeliveryControllerError) as exc:
        print(f"ack failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def retry_delivery(args: argparse.Namespace) -> int:
    try:
        controller = DeliveryController(JobStore(Path(args.store)))
        value = controller.retry(args.job_id, allow_duplicate=args.allow_duplicate)
    except (OSError, ValueError, KeyError, DeliveryControllerError) as exc:
        print(f"retry failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def resume(args: argparse.Namespace) -> int:
    try:
        controller = DeliveryController(JobStore(Path(args.store)))
        value = controller.resume_cli(
            args.job_id,
            args.session_id,
            timeout_seconds=args.timeout,
            allow_duplicate=args.allow_duplicate,
        )
    except (OSError, ValueError, KeyError, DeliveryControllerError) as exc:
        print(f"resume failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-snooze")
    parser.add_argument("--store", default=str(default_store()), help="job store directory")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    submit_parser = subparsers.add_parser("submit", help="submit an explicit non-interactive job")
    submit_parser.add_argument("--command", help="exact shell command string")
    submit_parser.add_argument("--cwd")
    submit_parser.add_argument("--shell")
    submit_parser.add_argument("--detach", action="store_true")
    submit_parser.add_argument(
        "--handoff-after",
        "--threshold",
        dest="handoff_after",
        type=float,
        help="return while the supervisor continues after this foreground wait",
    )
    submit_parser.add_argument("--log-limits", type=json.loads)
    submit_parser.add_argument("remainder", nargs=argparse.REMAINDER)
    submit_parser.set_defaults(function=submit)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("job_id")
    status_parser.set_defaults(function=status)

    result_parser = subparsers.add_parser("result")
    result_parser.add_argument("job_id")
    result_parser.set_defaults(function=result)

    cancel_parser = subparsers.add_parser("cancel")
    cancel_parser.add_argument("job_id")
    cancel_parser.add_argument("--grace-seconds", type=float, default=5.0)
    cancel_parser.set_defaults(function=cancel)

    list_parser = subparsers.add_parser("list")
    list_parser.set_defaults(function=list_jobs)

    recover_parser = subparsers.add_parser("recover", help="reconcile interrupted execution and delivery state")
    recover_parser.add_argument("job_id", nargs="?")
    recover_parser.set_defaults(function=recover)

    deliver_parser = subparsers.add_parser("deliver", help="write a manual completion event payload")
    deliver_parser.add_argument("job_id")
    deliver_parser.add_argument(
        "--allow-duplicate",
        action="store_true",
        help="permit another attempt after SENT_UNCONFIRMED",
    )
    deliver_parser.set_defaults(function=deliver)

    ack_parser = subparsers.add_parser("ack", help="acknowledge a manual completion event")
    ack_parser.add_argument("job_id")
    ack_parser.add_argument("--event-id")
    ack_parser.set_defaults(function=acknowledge)

    retry_parser = subparsers.add_parser("retry", help="request another delivery attempt")
    retry_parser.add_argument("job_id")
    retry_parser.add_argument("--allow-duplicate", action="store_true")
    retry_parser.set_defaults(function=retry_delivery)

    resume_parser = subparsers.add_parser(
        "resume",
        help="explicitly send a compact event through codex exec resume",
    )
    resume_parser.add_argument("job_id")
    resume_parser.add_argument("--session-id", required=True)
    resume_parser.add_argument("--timeout", type=float, default=300.0)
    resume_parser.add_argument("--allow-duplicate", action="store_true")
    resume_parser.set_defaults(function=resume)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.function(args))


if __name__ == "__main__":
    raise SystemExit(main())
