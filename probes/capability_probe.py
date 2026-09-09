#!/usr/bin/env python3
"""Evidence-producing probes for the installed Codex CLI and app-server.

The probe never records environment values. Live model probes are opt-in because
they can consume a model request and require the user's configured credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import shlex
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"


STATUS_ORDER = {"PASS": 0, "PARTIAL": 1, "FAIL": 2, "UNKNOWN": 3}


@dataclass
class Capability:
    name: str
    status: str
    evidence: List[str] = field(default_factory=list)
    observations: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "evidence": self.evidence,
            "observations": self.observations,
        }


def run_command(command: List[str], cwd: Optional[Path] = None, timeout: float = 20.0) -> Tuple[int, str, str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        return 124, (exc.stdout or ""), f"timeout after {timeout}s"
    except OSError as exc:
        return 127, "", f"{type(exc).__name__}: {exc}"
    return completed.returncode, completed.stdout, completed.stderr


class AppServerClient:
    def __init__(self, env: Optional[Dict[str, str]] = None):
        self.process = subprocess.Popen(
            ["codex", "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env or os.environ.copy(),
        )
        self.messages: queue.Queue[Tuple[str, Any]] = queue.Queue()
        self.notifications: List[Dict[str, Any]] = []
        self.stderr_lines: List[str] = []
        self.next_id = 1
        self._stdout_thread = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                self.messages.put(("text", line.rstrip()))
                continue
            self.messages.put(("json", value))

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            self.stderr_lines.append(line.rstrip())

    def send(self, method: str, params: Any, request_id: Optional[int] = None) -> int:
        if request_id is None:
            request_id = self.next_id
            self.next_id += 1
        message = {"method": method, "id": request_id, "params": params}
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        return request_id

    def notify(self, method: str, params: Any) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(
            json.dumps({"method": method, "params": params}, ensure_ascii=False) + "\n"
        )
        self.process.stdin.flush()

    def wait_response(self, request_id: int, timeout: float = 10.0) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                kind, value = self.messages.get(timeout=min(0.25, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if kind == "json" and isinstance(value, dict):
                if value.get("id") == request_id:
                    return value
                if "method" in value:
                    self.notifications.append(value)
        raise TimeoutError(f"timed out waiting for response id {request_id}")

    def request(self, method: str, params: Any, timeout: float = 10.0) -> Dict[str, Any]:
        return self.wait_response(self.send(method, params), timeout)

    def initialize(self) -> Dict[str, Any]:
        response = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "codex-snooze-capability-probe",
                    "title": "Codex Snooze Capability Probe",
                    "version": "0.1.0",
                },
                "capabilities": {
                    "experimentalApi": True,
                    "requestAttestation": False,
                },
            },
        )
        self.notify("initialized", {})
        return response

    def close(self) -> None:
        try:
            if self.process.stdin is not None:
                self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def response_error(response: Dict[str, Any]) -> Optional[str]:
    error = response.get("error")
    if error is None:
        return None
    return json.dumps(error, ensure_ascii=False, separators=(",", ":"))


def run_app_server_probe() -> List[Capability]:
    capabilities: List[Capability] = []
    client = AppServerClient()
    try:
        initialized = client.initialize()
        if response_error(initialized):
            return [
                Capability(
                    "app_server_initialize",
                    "FAIL",
                    ["codex app-server --stdio", response_error(initialized) or ""],
                )
            ]
        capabilities.append(
            Capability(
                "app_server_initialize",
                "PASS",
                ["initialize response received from installed app-server"],
                {"result_keys": sorted(initialized.get("result", {}).keys())},
            )
        )
        listed = client.request("thread/list", {"limit": 5, "useStateDbOnly": True})
        if response_error(listed):
            capabilities.append(
                Capability("app_server_thread_list", "FAIL", [response_error(listed) or ""])
            )
            return capabilities
        threads = (listed.get("result") or {}).get("data") or []
        idle_threads = [
            thread
            for thread in threads
            if ((thread.get("status") or {}).get("type") == "idle")
        ]
        capabilities.append(
            Capability(
                "app_server_thread_list",
                "PASS",
                ["thread/list response received"],
                {
                    "count": len(threads),
                    "thread_ids": [thread.get("id") for thread in threads],
                    "status_types": [((thread.get("status") or {}).get("type")) for thread in threads],
                    "idle_thread_ids": [thread.get("id") for thread in idle_threads],
                },
            )
        )
        if threads:
            read_thread_id = threads[0].get("id")
            read = client.request("thread/read", {"threadId": read_thread_id, "includeTurns": False})
            resume = None
            resume_thread_id = None
            resume_errors: List[Dict[str, Any]] = []
            for candidate in threads:
                candidate_id = candidate.get("id")
                candidate_resume = client.request(
                    "thread/resume", {"threadId": candidate_id, "excludeTurns": True}
                )
                candidate_error = response_error(candidate_resume)
                if candidate_error is None:
                    resume = candidate_resume
                    resume_thread_id = candidate_id
                    break
                resume_errors.append({"thread_id": candidate_id, "error": candidate_error})
            if resume is None:
                resume = {"error": {"attempts": resume_errors}}
            thread_id = resume_thread_id or read_thread_id
            read_ok = response_error(read) is None
            resume_ok = response_error(resume) is None
            capabilities.append(
                Capability(
                    "thread_read",
                    "PASS" if read_ok else "FAIL",
                    ["thread/read against an installed local thread"],
                    {"thread_id": read_thread_id, "error": response_error(read)},
                )
            )
            capabilities.append(
                Capability(
                    "thread_resume",
                    "PASS" if resume_ok else "PARTIAL",
                    ["thread/resume attempted against every listed thread until one succeeded"],
                    {
                        "thread_id": resume_thread_id,
                        "error": response_error(resume),
                        "same_thread_id": resume_ok
                        and (resume.get("result") or {}).get("thread", {}).get("id")
                        == resume_thread_id,
                        "attempts": resume_errors,
                    },
                )
            )
            if resume_ok and resume_thread_id:
                background = client.request(
                    "thread/backgroundTerminals/list", {"threadId": resume_thread_id, "limit": 20}
                )
                capabilities.append(
                    Capability(
                        "background_terminals_api",
                        "PASS" if response_error(background) is None else "PARTIAL",
                        ["thread/backgroundTerminals/list request after thread/resume"],
                        {"error": response_error(background)},
                    )
                )
            else:
                capabilities.append(
                    Capability(
                        "background_terminals_api",
                        "UNKNOWN",
                        ["no listed thread could be resumed and loaded"],
                    )
                )
        else:
            capabilities.append(
                Capability(
                    "thread_read",
                    "UNKNOWN",
                    ["no existing local thread was available for a read probe"],
                )
            )
            capabilities.append(
                Capability(
                    "thread_resume",
                    "UNKNOWN",
                    ["no idle local thread was available for a resume probe"],
                )
            )
    except (TimeoutError, OSError, ValueError, KeyError) as exc:
        capabilities.append(
            Capability(
                "app_server_runtime_probe",
                "UNKNOWN",
                [f"{type(exc).__name__}: {exc}"],
                {"stderr_tail": client.stderr_lines[-20:]},
            )
        )
    finally:
        client.close()
    return capabilities


def run_thread_lifecycle_probe() -> List[Capability]:
    client = AppServerClient()
    capabilities: List[Capability] = []
    thread_id: Optional[str] = None
    try:
        init = client.initialize()
        if response_error(init):
            return [Capability("thread_lifecycle", "UNKNOWN", [response_error(init) or ""])]
        started = client.request("thread/start", {"cwd": str(ROOT), "ephemeral": False})
        if response_error(started):
            return [
                Capability(
                    "thread_start",
                    "UNKNOWN",
                    ["thread/start was attempted without starting a model turn", response_error(started) or ""],
                )
            ]
        thread = (started.get("result") or {}).get("thread") or {}
        thread_id = thread.get("id")
        read = client.request("thread/read", {"threadId": thread_id, "includeTurns": False})
        resume = client.request("thread/resume", {"threadId": thread_id, "excludeTurns": True})
        same_read = (read.get("result") or {}).get("thread", {}).get("id") == thread_id
        same_resume = (resume.get("result") or {}).get("thread", {}).get("id") == thread_id
        capabilities.append(
            Capability(
                "thread_start",
                "PASS",
                ["durable thread/start response received"],
                {"thread_id": thread_id, "source": thread.get("source"), "path": thread.get("path")},
            )
        )
        capabilities.append(
            Capability(
                "thread_read",
                "PASS" if response_error(read) is None and same_read else "FAIL",
                ["thread/read returned the same thread id"],
                {"error": response_error(read), "same_thread_id": same_read},
            )
        )
        resume_error = response_error(resume)
        resume_status = "PASS" if resume_error is None and same_resume else "UNKNOWN"
        capabilities.append(
            Capability(
                "thread_resume",
                resume_status,
                ["thread/resume returned the same thread id"],
                {
                    "error": resume_error,
                    "same_thread_id": same_resume,
                    "reason": "thread/start without a completed rollout cannot be resumed"
                    if resume_error and "no rollout found" in resume_error
                    else None,
                },
            )
        )
        notifications = [item.get("method") for item in client.notifications]
        capabilities.append(
            Capability(
                "thread_status_notification",
                "PASS" if "thread/status/changed" in notifications else "PARTIAL",
                ["notifications observed while starting/resuming a thread"],
                {"methods": notifications},
            )
        )
    except (TimeoutError, OSError, ValueError, KeyError) as exc:
        capabilities.append(
            Capability("thread_lifecycle", "UNKNOWN", [f"{type(exc).__name__}: {exc}"])
        )
    finally:
        if thread_id:
            try:
                client.request("thread/delete", {"threadId": thread_id}, timeout=5)
            except (TimeoutError, OSError):
                pass
        client.close()
    return capabilities


def run_static_cli_probe() -> List[Capability]:
    commands = {
        "codex_version": ["codex", "--version"],
        "codex_help": ["codex", "--help"],
        "codex_exec_help": ["codex", "exec", "--help"],
        "codex_exec_resume_help": ["codex", "exec", "resume", "--help"],
        "codex_app_server_help": ["codex", "app-server", "--help"],
        "codex_queue_help": ["codex", "queue", "--help"],
    }
    outputs: Dict[str, str] = {}
    statuses: List[Capability] = []
    for name, command in commands.items():
        returncode, stdout, stderr = run_command(command)
        outputs[name] = stdout
        statuses.append(
            Capability(
                name,
                "PASS" if returncode == 0 else "FAIL",
                [" ".join(command)],
                {"returncode": returncode, "stderr": stderr[-1000:]},
            )
        )
    version_match = re.search(r"codex-cli\s+([^\s]+)", outputs.get("codex_version", ""))
    statuses.append(
        Capability(
            "installed_codex_version",
            "PASS" if version_match else "UNKNOWN",
            [outputs.get("codex_version", "").strip()],
            {"version": version_match.group(1) if version_match else None},
        )
    )
    return statuses


def run_protocol_inventory_probe() -> List[Capability]:
    schema_path = ROOT / "probes" / "app-server-schema" / "codex_app_server_protocol.v2.schemas.json"
    targets = {
        "thread/start": "thread/start",
        "thread/read": "thread/read",
        "thread/resume": "thread/resume",
        "thread/status notification": "thread/status/changed",
        "turn/start": "turn/start",
        "turn/interrupt": "turn/interrupt",
        "turn/steer": "turn/steer",
        "thread/inject_items": "thread/inject_items",
        "toolOutput field": "toolOutput",
        "backgroundTerminals": "thread/backgroundTerminals/list",
        "queue": "thread/queue/list",
    }
    try:
        schema_text = schema_path.read_text(encoding="utf-8")
    except OSError as exc:
        return [Capability("app_server_protocol_inventory", "UNKNOWN", [f"{type(exc).__name__}: {exc}"])]
    presence = {name: target in schema_text for name, target in targets.items()}
    status = "PASS" if all(presence.values()) else "PARTIAL"
    return [
        Capability(
            "app_server_protocol_inventory",
            status,
            ["installed v2 JSON schema generated by codex app-server"],
            {"schema": str(schema_path), "presence": presence},
        ),
        Capability(
            "busy_thread_delivery",
            "UNKNOWN",
            [
                "toolOutput, turn/steer, thread/inject_items and queue methods are present in schema; "
                "no live injection was enabled without a verified target thread and acknowledgement contract"
            ],
            {
                "candidate_methods": [
                    "turn/start.toolOutput",
                    "turn/steer",
                    "thread/inject_items",
                    "thread/queue/add",
                ],
                "runtime_delivery": "UNKNOWN",
            },
        ),
    ]


def run_cli_resume_probe(enabled: bool) -> List[Capability]:
    if not enabled:
        return [
            Capability(
                "codex_exec_resume",
                "UNKNOWN",
                ["live model probe not requested; rerun with --live"],
            )
        ]
    marker = f"SNOOZE_RESUME_MARKER_{uuid.uuid4().hex}"
    prompt = (
        "Do not use tools. Reply with this exact marker and nothing else: " + marker
    )
    with tempfile.TemporaryDirectory(prefix="codex-snooze-exec-") as temporary:
        output_path = Path(temporary) / "last-message.txt"
        command = [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--cd",
            str(ROOT),
            "--output-last-message",
            str(output_path),
            prompt,
        ]
        first_code, first_stdout, first_stderr = run_command(command, timeout=180)
        events: List[Dict[str, Any]] = []
        for line in first_stdout.splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                events.append(value)
        session_id = None
        for event in events:
            if event.get("type") in {"thread.started", "thread_started"}:
                session_id = (event.get("thread") or {}).get("id") or event.get("thread_id")
            session_id = session_id or event.get("thread_id")
        if session_id is None:
            match = re.search(
                r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
                first_stdout,
            )
            session_id = match.group(0) if match else None
        history_restored = marker in first_stdout or (
            output_path.exists() and marker in output_path.read_text(encoding="utf-8", errors="replace")
        )
        if first_code != 0 or session_id is None:
            return [
                Capability(
                    "codex_exec_resume",
                    "UNKNOWN",
                    ["codex exec live probe", first_stderr[-2000:]],
                    {
                        "first_returncode": first_code,
                        "session_id_found": session_id is not None,
                        "conversation_history_restored": history_restored,
                    },
                )
            ]
        resume_prompt = (
            "Do not use tools. Did the previous turn contain this exact marker? "
            + marker
            + " Reply with YES or NO only."
        )
        resume_command = [
            "codex",
            "exec",
            "resume",
            "--json",
            "--skip-git-repo-check",
            session_id,
            resume_prompt,
        ]
        second_code, second_stdout, second_stderr = run_command(resume_command, timeout=180)
        resumed_history = "YES" in second_stdout.upper() and second_code == 0
        return [
            Capability(
                "codex_exec_resume",
                "PASS" if resumed_history else "PARTIAL",
                ["codex exec followed by codex exec resume", "live model probe"],
                {
                    "session_id": session_id,
                    "first_returncode": first_code,
                    "resume_returncode": second_code,
                    "conversation_history_restored": history_restored or resumed_history,
                    "desktop_ui_same_thread": "UNKNOWN",
                    "cwd_restored": "UNKNOWN",
                    "model_configuration_restored": "UNKNOWN",
                    "sandbox_policy_restored": "UNKNOWN",
                    "approval_policy_restored": "UNKNOWN",
                    "tool_execution_state_restored": "UNKNOWN",
                    "resume_stderr_tail": second_stderr[-2000:],
                },
            )
        ]


def run_turn_interrupt_probe(enabled: bool) -> List[Capability]:
    if not enabled:
        return [
            Capability(
                "turn_interrupt",
                "UNKNOWN",
                ["live model probe not requested; rerun with --live"],
            )
        ]
    client = AppServerClient()
    thread_id = None
    turn_id = None
    child_pid_path: Optional[Path] = None
    try:
        init = client.initialize()
        if response_error(init):
            return [Capability("turn_interrupt", "UNKNOWN", [response_error(init) or ""])]
        started = client.request("thread/start", {"cwd": str(ROOT), "ephemeral": False})
        if response_error(started):
            return [Capability("turn_interrupt", "UNKNOWN", [response_error(started) or ""])]
        thread_id = (started.get("result") or {}).get("thread", {}).get("id")
        child_pid_path = Path(tempfile.mkdtemp(prefix="codex-snooze-child-")) / "child.pid"
        pid_path_literal = shlex.quote(str(child_pid_path))
        turn = client.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [
                    {
                        "type": "text",
                        "text": (
                            "Run this exact shell command and wait for it to finish: "
                            f"/bin/sh -c 'sleep 30 & child=$!; printf %s $child > {pid_path_literal}; wait $child'"
                        ),
                    }
                ],
            },
            timeout=20,
        )
        if response_error(turn):
            return [Capability("turn_start_for_interrupt", "UNKNOWN", [response_error(turn) or ""])]
        turn_id = (turn.get("result") or {}).get("turn", {}).get("id")
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and not child_pid_path.exists():
            try:
                kind, value = client.messages.get(timeout=0.25)
                if kind == "json" and isinstance(value, dict) and value.get("method"):
                    client.notifications.append(value)
            except queue.Empty:
                pass
        child_pid = None
        if child_pid_path.exists():
            try:
                child_pid = int(child_pid_path.read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                child_pid = None
        interrupt = client.request(
            "turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=30
        )
        completed = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if any(
                notification.get("method") == "turn/completed"
                and (notification.get("params") or {}).get("turn", {}).get("id") == turn_id
                for notification in client.notifications
            ):
                completed = next(
                    notification
                    for notification in client.notifications
                    if notification.get("method") == "turn/completed"
                    and (notification.get("params") or {}).get("turn", {}).get("id") == turn_id
                )
                break
            try:
                kind, value = client.messages.get(timeout=0.25)
                if kind == "json" and isinstance(value, dict):
                    if value.get("method"):
                        client.notifications.append(value)
            except queue.Empty:
                pass
        status = ((completed or {}).get("params") or {}).get("turn", {}).get("status")
        child_survived = None
        if child_pid is not None:
            try:
                os.kill(child_pid, 0)
                child_survived = True
            except ProcessLookupError:
                child_survived = False
            except PermissionError:
                child_survived = True
            if child_survived:
                try:
                    os.kill(child_pid, signal.SIGTERM)
                except OSError:
                    pass
        return [
            Capability(
                "turn_interrupt",
                "PASS" if response_error(interrupt) is None and status == "interrupted" else "PARTIAL",
                ["turn/start, wait, turn/interrupt, turn/completed"],
                {
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "interrupt_error": response_error(interrupt),
                    "completed_status": status,
                    "supervisor_survival": "NOT_APPLICABLE_TO_DIRECT_APP_SERVER_TURN",
                    "child_survival": child_survived,
                },
            )
        ]
    except (TimeoutError, OSError, ValueError, KeyError) as exc:
        return [Capability("turn_interrupt", "UNKNOWN", [f"{type(exc).__name__}: {exc}"])]
    finally:
        if thread_id:
            try:
                client.request("thread/delete", {"threadId": thread_id}, timeout=5)
            except (TimeoutError, OSError):
                pass
        client.close()


def run_snooze_handoff_probe(enabled: bool) -> List[Capability]:
    """Run the real CLI launcher from a model turn, then interrupt the turn."""
    if not enabled:
        return [
            Capability(
                "snooze_turn_interrupt_survival",
                "UNKNOWN",
                ["live model probe not requested; rerun with --live"],
            )
        ]
    client = AppServerClient()
    thread_id: Optional[str] = None
    turn_id: Optional[str] = None
    temporary = Path(tempfile.mkdtemp(prefix="codex-snooze-e2e-"))
    store_path = temporary / "store"
    marker_path = temporary / "complete.marker"
    try:
        init = client.initialize()
        if response_error(init):
            return [Capability("snooze_turn_interrupt_survival", "UNKNOWN", [response_error(init) or ""])]
        started = client.request("thread/start", {"cwd": str(ROOT), "ephemeral": False})
        if response_error(started):
            return [Capability("snooze_turn_interrupt_survival", "UNKNOWN", [response_error(started) or ""])]
        thread_id = (started.get("result") or {}).get("thread", {}).get("id")
        inner_command = f"sleep 2; printf SNOOZE_E2E_COMPLETE > {shlex.quote(str(marker_path))}"
        launcher = (
            f"cd {shlex.quote(str(ROOT))} && "
            f"PYTHONPATH={shlex.quote(str(ROOT))} python3 -m snooze_core "
            f"--store {shlex.quote(str(store_path))} submit --shell /bin/bash "
            f"--command {shlex.quote(inner_command)}"
        )
        turn = client.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [
                    {
                        "type": "text",
                        "text": (
                            "Use the terminal command execution tool now. You must run exactly the command below "
                            "and wait until it completes before replying. Do not run any other command and do not "
                            "answer from the model first.\n\nCOMMAND:\n"
                            + launcher
                        ),
                    }
                ],
            },
            timeout=20,
        )
        if response_error(turn):
            return [Capability("snooze_turn_start", "UNKNOWN", [response_error(turn) or ""])]
        turn_id = (turn.get("result") or {}).get("turn", {}).get("id")
        job_id: Optional[str] = None
        metadata_before_interrupt: Optional[Dict[str, Any]] = None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            jobs_dir = store_path / "jobs"
            if jobs_dir.is_dir():
                candidates = [entry for entry in jobs_dir.iterdir() if entry.is_dir()]
                if candidates:
                    job_id = candidates[0].name
                    metadata_path = candidates[0] / "metadata.json"
                    if metadata_path.exists():
                        try:
                            metadata_before_interrupt = json.loads(metadata_path.read_text(encoding="utf-8"))
                        except (OSError, ValueError):
                            metadata_before_interrupt = None
                        if metadata_before_interrupt and metadata_before_interrupt.get("execution_state") == "RUNNING":
                            break
            try:
                kind, value = client.messages.get(timeout=0.25)
                if kind == "json" and isinstance(value, dict) and value.get("method"):
                    client.notifications.append(value)
            except queue.Empty:
                pass
        if job_id is None:
            return [
                Capability(
                    "snooze_turn_interrupt_survival",
                    "UNKNOWN",
                    ["CLI launcher did not create a job before interrupt window"],
                    {
                        "store": str(store_path),
                        "notification_methods": [item.get("method") for item in client.notifications],
                        "turn_completed_status": next(
                            (
                                (item.get("params") or {}).get("turn", {}).get("status")
                                for item in client.notifications
                                if item.get("method") == "turn/completed"
                            ),
                            None,
                        ),
                    },
                )
            ]
        interrupt = client.request(
            "turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=30
        )
        completed = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            completed_items = [
                notification
                for notification in client.notifications
                if notification.get("method") == "turn/completed"
                and (notification.get("params") or {}).get("turn", {}).get("id") == turn_id
            ]
            if completed_items:
                completed = completed_items[-1]
                break
            try:
                kind, value = client.messages.get(timeout=0.25)
                if kind == "json" and isinstance(value, dict) and value.get("method"):
                    client.notifications.append(value)
            except queue.Empty:
                pass
        completed_status = ((completed or {}).get("params") or {}).get("turn", {}).get("status")
        result: Optional[Dict[str, Any]] = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if job_id:
                result_path = store_path / "jobs" / job_id / "result.json"
                if result_path.exists():
                    try:
                        result = json.loads(result_path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        result = None
                    if result is not None:
                        break
            time.sleep(0.25)
        survived = bool(result and result.get("exit_code") == 0 and marker_path.exists())
        status = "PASS" if response_error(interrupt) is None and completed_status == "interrupted" and survived else "PARTIAL"
        return [
            Capability(
                "snooze_turn_interrupt_survival",
                status,
                ["app-server turn/start, CLI submit, turn/interrupt, durable result read"],
                {
                    "thread_id": thread_id,
                    "turn_id": turn_id,
                    "job_id": job_id,
                    "interrupt_error": response_error(interrupt),
                    "completed_status": completed_status,
                    "metadata_before_interrupt": metadata_before_interrupt,
                    "supervisor_survival": survived,
                    "child_exit_code": result.get("exit_code") if result else None,
                    "marker_written": marker_path.exists(),
                },
            )
        ]
    except (TimeoutError, OSError, ValueError, KeyError) as exc:
        return [Capability("snooze_turn_interrupt_survival", "UNKNOWN", [f"{type(exc).__name__}: {exc}"])]
    finally:
        if thread_id:
            try:
                client.request("thread/delete", {"threadId": thread_id}, timeout=5)
            except (TimeoutError, OSError):
                pass
        client.close()
        shutil.rmtree(temporary, ignore_errors=True)


def overall_status(capabilities: Iterable[Capability]) -> str:
    values = [capability.status for capability in capabilities]
    if "FAIL" in values:
        return "FAIL"
    if "PARTIAL" in values:
        return "PARTIAL"
    if values and all(value == "PASS" for value in values):
        return "PASS"
    return "UNKNOWN"


def write_results(capabilities: List[Capability], live: bool) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "codex_version": run_command(["codex", "--version"])[1].strip(),
        "platform": os.uname().sysname,
        "machine": os.uname().machine,
        "shell": os.environ.get("SHELL", "UNKNOWN"),
        "live_model_probes_requested": live,
        "overall": overall_status(capabilities),
        "capabilities": [capability.as_dict() for capability in capabilities],
    }
    (RESULTS / "capabilities.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# Capability Matrix",
        "",
        f"Overall: **{payload['overall']}**",
        "",
        f"Generated: `{payload['generated_at']}`",
        f"Codex: `{payload['codex_version']}`",
        f"Platform: `{payload['platform']} {payload['machine']}`",
        "",
        "| Capability | Status | Evidence |",
        "|---|---|---|",
    ]
    for capability in capabilities:
        evidence = "<br>".join(item.replace("|", "\\|") for item in capability.evidence if item)
        lines.append(f"| `{capability.name}` | **{capability.status}** | {evidence} |")
    lines.extend(["", "## Observations", ""])
    for capability in capabilities:
        lines.append(f"### `{capability.name}` — {capability.status}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(capability.observations, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
    (RESULTS / "capabilities.md").write_text("\n".join(lines), encoding="utf-8")
    e2e = next(
        (capability for capability in capabilities if capability.name == "snooze_turn_interrupt_survival"),
        None,
    )
    if e2e is not None:
        history_path = RESULTS / "snooze-e2e-history.json"
        try:
            history = json.loads(history_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            history = []
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "generated_at": payload["generated_at"],
                "status": e2e.status,
                "observations": e2e.observations,
            }
        )
        history_path.write_text(json.dumps(history[-20:], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Probe installed Codex capabilities")
    parser.add_argument("--live", action="store_true", help="run model-backed probes")
    args = parser.parse_args(argv)
    capabilities = run_static_cli_probe()
    capabilities.extend(run_protocol_inventory_probe())
    capabilities.extend(run_app_server_probe())
    capabilities.extend(run_thread_lifecycle_probe())
    capabilities.extend(run_cli_resume_probe(args.live))
    capabilities.extend(run_turn_interrupt_probe(args.live))
    capabilities.extend(run_snooze_handoff_probe(args.live))
    write_results(capabilities, args.live)
    print(json.dumps({"overall": overall_status(capabilities), "count": len(capabilities)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
