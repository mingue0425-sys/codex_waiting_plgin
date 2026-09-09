from __future__ import annotations

"""Explicit long-job handoff orchestration for Snooze-owned threads.

This module is deliberately opt-in.  It does not classify commands, rewrite
tool calls or intercept PreToolUse.  A caller starts a turn containing an
explicit ``codex-snooze handoff`` command, then this controller observes the
structured marker, closes only the current Codex turn, waits on the durable job
result and starts the continuation on the same owned thread.
"""

import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from snooze_core.models import ExecutionState
from snooze_core.store import JobStore

from .agent import AgentController, AgentControllerError
from .app_server_process import ServerRequest
from .completion_router import CompletionRouter, CompletionRouterError
from .thread_registry import ThreadRegistry


class HandoffPhase(str, Enum):
    TURN_STARTED = "TURN_STARTED"
    HANDOFF_REQUESTED = "HANDOFF_REQUESTED"
    TURN_INTERRUPTING = "TURN_INTERRUPTING"
    TURN_INTERRUPTED = "TURN_INTERRUPTED"
    HANDOFF_TURN_CLOSED = "HANDOFF_TURN_CLOSED"
    WAITING_FOR_JOB = "WAITING_FOR_JOB"
    JOB_COMPLETED = "JOB_COMPLETED"
    CONTINUATION_STARTED = "CONTINUATION_STARTED"
    CONTINUATION_COMPLETED = "CONTINUATION_COMPLETED"
    SHORT_COMMAND_COMPLETED = "SHORT_COMMAND_COMPLETED"
    FAILED = "FAILED"


class HandoffControllerError(RuntimeError):
    pass


class DynamicHandoffTool:
    """Explicit experimental App Server tool backed by the handoff CLI.

    The model must opt into this named tool. The handler never rewrites a
    normal terminal item and never answers an approval request. It validates
    an argv vector, then asks the same owned App Server connection to run the
    ordinary ``codex-snooze handoff`` command through ``command/exec`` with an
    explicit workspace-write policy. This keeps the handoff path under the
    server's sandbox and approval machinery.
    """

    name = "codex_snooze_handoff"

    def __init__(
        self,
        store: JobStore,
        *,
        cwd: Path,
        launcher: Optional[Path] = None,
        process: Optional[Any] = None,
        sandbox_policy: Optional[Dict[str, Any]] = None,
        allow_local_fallback: bool = False,
    ) -> None:
        self.store = store
        self.cwd = Path(cwd).resolve()
        self.launcher = (
            Path(launcher).resolve()
            if launcher is not None
            else Path(__file__).resolve().parents[1] / "scripts" / "codex-snooze"
        )
        self.process = process
        self.sandbox_policy = dict(sandbox_policy or {
            "type": "workspaceWrite",
            "writableRoots": [str(self.cwd)],
        })
        self.allow_local_fallback = bool(allow_local_fallback)
        self.calls: List[Dict[str, Any]] = []

    def bind_process(self, process: Any) -> None:
        """Bind the tool to its owning App Server connection."""
        self.process = process

    @classmethod
    def spec(cls) -> Dict[str, Any]:
        return {
            "type": "function",
            "name": cls.name,
            "description": (
                "Run one explicit non-interactive command through Codex Snooze. "
                "The command may detach after the requested foreground threshold. "
                "Never use this tool more than once for the same command."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "argv vector for the command",
                    },
                    "threshold_seconds": {"type": "number", "minimum": 0},
                    "cwd": {"type": "string"},
                },
                "required": ["command", "threshold_seconds"],
                "additionalProperties": False,
            },
        }

    def __call__(self, request: ServerRequest) -> Dict[str, Any]:
        # Only the dynamic-tool request is in this handler's authority. In
        # particular, approval requests must be rejected by AppServerProcess
        # rather than accidentally receiving a tool-shaped response.
        if request.method != "item/tool/call":
            return None  # type: ignore[return-value]
        params = request.params if isinstance(request.params, dict) else {}
        if params.get("tool") != self.name:
            return None  # type: ignore[return-value]
        arguments = params.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError as exc:
                return self._failure(f"arguments are not valid JSON: {exc}")
        if not isinstance(arguments, dict):
            return self._failure("arguments must be an object")
        command = arguments.get("command")
        if isinstance(command, str):
            try:
                command = shlex.split(command)
            except ValueError as exc:
                return self._failure(f"command is not valid shell syntax: {exc}")
        if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
            return self._failure("command must be a non-empty argv list")
        try:
            threshold = float(arguments.get("threshold_seconds"))
        except (TypeError, ValueError):
            return self._failure("threshold_seconds must be numeric")
        requested_cwd = Path(str(arguments.get("cwd") or self.cwd)).expanduser().resolve()
        if requested_cwd != self.cwd:
            return self._failure("dynamic handoff cwd is outside the owned workspace")
        if threshold < 0:
            return self._failure("threshold_seconds must be non-negative")
        launch = [
            str(self.launcher),
            "--store",
            str(self.store.root),
            "handoff",
            "--threshold",
            str(threshold),
            "--cwd",
            str(self.cwd),
            "--",
            *command,
        ]
        self.calls.append(
            {
                "request_id": request.request_id,
                "command": list(command),
                "threshold_seconds": threshold,
                "cwd": str(self.cwd),
            }
        )
        completed = self._run_through_server(launch, threshold=threshold)
        if completed.get("error") is not None:
            return self._failure(str(completed["error"]), stdout=str(completed.get("stdout") or "")[-4000:])
        stdout = str(completed.get("stdout") or "")
        stderr = str(completed.get("stderr") or "")
        exit_code = completed.get("exit_code")
        marker = find_detached_marker(stdout)
        if exit_code != 0:
            return self._failure(
                f"handoff launcher returned {exit_code}: {stderr[-2000:]}",
                stdout=stdout[-4000:],
            )
        if marker is None:
            return {
                "result": {
                    "success": True,
                    "contentItems": [{"type": "inputText", "text": stdout[-4000:]}],
                }
            }
        return {
            "result": {
                "success": True,
                "contentItems": [{"type": "inputText", "text": json.dumps(marker, ensure_ascii=False)}],
            }
        }

    def _run_through_server(self, launch: List[str], *, threshold: float) -> Dict[str, Any]:
        """Run the launcher with the App Server's command/exec sandbox."""
        if self.process is not None:
            try:
                response = self.process.request(
                    "command/exec",
                    {
                        "command": launch,
                        "cwd": str(self.cwd),
                        "disableTimeout": True,
                        "sandboxPolicy": self.sandbox_policy,
                    },
                    timeout=max(60.0, threshold + 60.0),
                )
            except Exception as exc:
                return {"error": f"nested command/exec failed: {type(exc).__name__}: {exc}"}
            if response.get("error") is not None:
                return {"error": f"nested command/exec rejected: {response.get('error')}"}
            result = response.get("result")
            if not isinstance(result, dict):
                return {"error": "nested command/exec returned no structured result"}
            return {
                "exit_code": result.get("exitCode"),
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
            }
        if not self.allow_local_fallback:
            return {"error": "handoff tool is not bound to an owned App Server"}
        # This opt-in branch exists only for isolated unit tests. Production
        # callers must bind an AppServerProcess so the command uses the server
        # sandbox and approval policy.
        try:
            completed = subprocess.run(
                launch,
                cwd=self.cwd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=max(30.0, threshold + 30.0),
                check=False,
                env=os.environ.copy(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"error": f"local test fallback failed: {type(exc).__name__}: {exc}"}
        return {"exit_code": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}

    @staticmethod
    def _failure(message: str, *, stdout: str = "") -> Dict[str, Any]:
        return {
            "result": {
                "success": False,
                "contentItems": [{"type": "inputText", "text": json.dumps({"error": message, "stdout": stdout})}],
            }
        }


@dataclass
class HandoffObservation:
    phase: HandoffPhase
    thread_id: str
    turn_id: str
    marker: Optional[Dict[str, Any]] = None
    item_id: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    turn: Optional[Dict[str, Any]] = None
    handoff_monotonic_ns: Optional[int] = None
    job_completed_monotonic_ns: Optional[int] = None
    error: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase.value,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "marker": self.marker,
            "item_id": self.item_id,
            "result": self.result,
            "turn": self.turn,
            "handoff_monotonic_ns": self.handoff_monotonic_ns,
            "job_completed_monotonic_ns": self.job_completed_monotonic_ns,
            "error": self.error,
        }


def _iter_values(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for item in value.values():
            yield from _iter_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_values(item)


def _marker_from_text(text: str) -> Optional[Dict[str, Any]]:
    decoder = json.JSONDecoder()
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except ValueError:
            value = None
            for index, char in enumerate(candidate):
                if char != "{":
                    continue
                try:
                    value, _ = decoder.raw_decode(candidate[index:])
                    break
                except ValueError:
                    continue
        if isinstance(value, dict) and value.get("codex_snooze") is True:
            return value
    return None


def find_detached_marker(value: Any) -> Optional[Dict[str, Any]]:
    """Find only the structured handoff marker; never parse prose."""
    for item in _iter_values(value):
        if isinstance(item, dict) and item.get("codex_snooze") is True:
            if item.get("state") == "DETACHED" and item.get("job_id"):
                return dict(item)
        if isinstance(item, str):
            marker = _marker_from_text(item)
            if marker and marker.get("state") == "DETACHED" and marker.get("job_id"):
                return marker
    return None


def _item_id(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        item = value.get("item")
        if isinstance(item, dict) and item.get("id"):
            return str(item["id"])
        if value.get("id") and (value.get("type") == "commandExecution" or value.get("itemType") == "commandExecution"):
            return str(value["id"])
        for child in value.values():
            result = _item_id(child)
            if result:
                return result
    elif isinstance(value, (list, tuple)):
        for child in value:
            result = _item_id(child)
            if result:
                return result
    return None


class HandoffController:
    """Coordinate one explicit handoff and its same-thread continuation."""

    def __init__(
        self,
        controller: AgentController,
        store: JobStore,
        registry: Optional[ThreadRegistry] = None,
        *,
        router_path: Optional[Path] = None,
        poll_interval: float = 0.05,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        self.controller = controller
        self.store = store
        self.registry = registry or controller.registry
        self.router = CompletionRouter(router_path or store.root / "app-server-completions.json")
        self.poll_interval = poll_interval
        self.phase = HandoffPhase.TURN_STARTED
        self.handoff: Optional[HandoffObservation] = None
        self._known_jobs = set(store.list_job_ids())

    def _new_job_marker(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        for job_id in self.store.list_job_ids():
            if job_id in self._known_jobs:
                continue
            try:
                marker = self.store.read_handoff_marker(job_id)
            except (OSError, ValueError):
                continue
            if marker and marker.get("state") == "DETACHED":
                return marker, self._latest_command_item_id()
        return None, None

    def _latest_command_item_id(self) -> Optional[str]:
        for value in reversed(self.controller.process.notifications):
            if value.get("method") not in {"item/started", "item/completed"}:
                continue
            item = (value.get("params") or {}).get("item")
            if isinstance(item, dict) and item.get("type") == "commandExecution" and item.get("id"):
                return str(item["id"])
        for request in reversed(self.controller.process.server_requests):
            if request.method == "item/tool/call" and isinstance(request.params, dict):
                call_id = request.params.get("callId")
                if call_id:
                    return str(call_id)
        return None

    def _event_marker(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        values: List[Tuple[Any, Optional[str]]] = []
        values.extend((item, _item_id(item)) for item in self.controller.process.notifications)
        values.extend((item, _item_id(item)) for item in self.controller.process.events)
        for item, item_id in values:
            marker = find_detached_marker(item)
            if marker is not None:
                return marker, item_id
        return None, None

    def _find_marker(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        marker, item_id = self._event_marker()
        if marker is not None:
            return marker, item_id
        return self._new_job_marker()

    def _turn_closed(self, turn_id: str) -> Optional[Dict[str, Any]]:
        return self.controller.completed_turn(turn_id)

    def _close_current_turn(self, turn_id: str, observation: HandoffObservation) -> None:
        completed = self._turn_closed(turn_id)
        if completed is not None:
            observation.phase = HandoffPhase.HANDOFF_TURN_CLOSED
            observation.turn = completed
            return
        self.phase = HandoffPhase.TURN_INTERRUPTING
        observation.phase = self.phase
        try:
            self.controller.interrupt_turn(turn_id, timeout=30.0)
            self.phase = HandoffPhase.TURN_INTERRUPTED
            observation.phase = self.phase
        except AgentControllerError as exc:
            # A completion notification can win the race after the first
            # active check.  Treat only that race as a natural close.
            if self._turn_closed(turn_id) is None:
                raise
            observation.phase = HandoffPhase.HANDOFF_TURN_CLOSED
            observation.error = f"interrupt raced with completion: {exc}"
        observation.turn = self.controller.wait_turn(turn_id, timeout=60.0)

    def wait_for_handoff(self, turn_id: str, *, timeout: float = 60.0) -> HandoffObservation:
        if not self.controller.thread_id:
            raise HandoffControllerError("controller has no thread")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        thread_id = self.controller.thread_id
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            marker, item_id = self._find_marker()
            if marker is not None:
                job_id = str(marker["job_id"])
                observation = HandoffObservation(
                    phase=HandoffPhase.HANDOFF_REQUESTED,
                    thread_id=thread_id,
                    turn_id=str(turn_id),
                    marker=marker,
                    item_id=item_id,
                    handoff_monotonic_ns=time.monotonic_ns(),
                )
                self.handoff = observation
                self.phase = HandoffPhase.HANDOFF_REQUESTED
                mapping = self.registry.record_handoff(
                    thread_id,
                    turn_id=str(turn_id),
                    item_id=item_id,
                    job_id=job_id,
                    marker=marker,
                    state=HandoffPhase.HANDOFF_REQUESTED.value,
                )
                if mapping.get("item_id") is not None and observation.item_id is None:
                    observation.item_id = mapping["item_id"]
                self._close_current_turn(str(turn_id), observation)
                self.registry.update_handoff(
                    thread_id,
                    job_id,
                    state=observation.phase.value,
                    turn_status=(observation.turn or {}).get("status"),
                )
                return observation
            completed = self._turn_closed(str(turn_id))
            if completed is not None:
                # A short command can finish before the threshold.  Preserve
                # its normal result path and avoid sending an unnecessary
                # interrupt to an already closed turn.
                for job_id in self.store.list_job_ids():
                    if job_id in self._known_jobs:
                        continue
                    result = self.store.read_result(job_id)
                    if result is not None:
                        return HandoffObservation(
                            phase=HandoffPhase.SHORT_COMMAND_COMPLETED,
                            thread_id=thread_id,
                            turn_id=str(turn_id),
                            result=result,
                            turn=completed,
                        )
                raise HandoffControllerError("turn completed without a structured handoff marker")
            time.sleep(min(self.poll_interval, max(0.01, deadline - time.monotonic())))
        raise HandoffControllerError("handoff marker was not observed before timeout")

    def wait_for_job(self, observation: HandoffObservation, *, timeout: float = 120.0) -> Dict[str, Any]:
        if observation.marker is None:
            raise HandoffControllerError("handoff observation has no marker")
        job_id = str(observation.marker["job_id"])
        self.phase = HandoffPhase.WAITING_FOR_JOB
        observation.phase = self.phase
        deadline = time.monotonic() + timeout
        terminal = {
            ExecutionState.COMPLETED.value,
            ExecutionState.COMPLETED_STALE.value,
            ExecutionState.FAILED.value,
            ExecutionState.CANCELLED.value,
            ExecutionState.LOST.value,
            ExecutionState.ORPHANED.value,
        }
        while time.monotonic() < deadline:
            result = self.store.read_result(job_id)
            if result is not None and result.get("execution_state") in terminal:
                observation.result = result
                observation.job_completed_monotonic_ns = time.monotonic_ns()
                self.phase = HandoffPhase.JOB_COMPLETED
                observation.phase = self.phase
                self.registry.update_handoff(
                    observation.thread_id,
                    job_id,
                    state=self.phase.value,
                    completion_event_id=result.get("completion_event_id"),
                    result_sha256=result.get("result_sha256"),
                )
                return result
            time.sleep(min(self.poll_interval, max(0.01, deadline - time.monotonic())))
        raise HandoffControllerError(f"job did not produce a terminal result: {job_id}")

    def continue_after_job(
        self,
        observation: HandoffObservation,
        *,
        prompt: str,
        allow_duplicate: bool = False,
        request_timeout: float = 30.0,
        wait_timeout: float = 300.0,
    ) -> Dict[str, Any]:
        result = observation.result or self.wait_for_job(observation)
        event_id = result.get("completion_event_id")
        if not event_id:
            raise HandoffControllerError("terminal result has no completion event id")
        self.phase = HandoffPhase.CONTINUATION_STARTED
        observation.phase = self.phase
        self.registry.update_handoff(observation.thread_id, str(result["job_id"]), state=self.phase.value)
        try:
            routed = self.router.route(
                self.controller,
                event_id=str(event_id),
                job_id=str(result["job_id"]),
                result_sha256=result.get("result_sha256"),
                prompt=prompt,
                allow_duplicate=allow_duplicate,
                request_timeout=request_timeout,
                wait_timeout=wait_timeout,
            )
        except (CompletionRouterError, AgentControllerError) as exc:
            self.phase = HandoffPhase.FAILED
            observation.phase = self.phase
            observation.error = f"continuation failed: {type(exc).__name__}: {exc}"
            self.registry.update_handoff(observation.thread_id, str(result["job_id"]), state=self.phase.value, error=observation.error)
            raise HandoffControllerError(observation.error) from exc
        self.phase = HandoffPhase.CONTINUATION_COMPLETED
        observation.phase = self.phase
        observation.turn = routed.get("turn")
        self.registry.update_handoff(
            observation.thread_id,
            str(result["job_id"]),
            state=self.phase.value,
            continuation_turn_id=routed.get("turn_id"),
            delivery_state=routed.get("state"),
        )
        return routed

    @staticmethod
    def model_activity_between(
        process: Any,
        start_monotonic_ns: Optional[int],
        end_monotonic_ns: Optional[int],
    ) -> List[Dict[str, Any]]:
        """Return model activity observed during the OS-only wait interval."""
        if start_monotonic_ns is None or end_monotonic_ns is None:
            return []
        prefixes = (
            "turn/started",
            "item/agentMessage",
            "item/reasoning",
            "item/tool",
        )
        return [
            event
            for event in process.events
            if start_monotonic_ns <= int(event.get("monotonic_ns", 0)) <= end_monotonic_ns
            and str(event.get("method", "")).startswith(prefixes)
        ]


__all__ = [
    "DynamicHandoffTool",
    "HandoffController",
    "HandoffControllerError",
    "HandoffObservation",
    "HandoffPhase",
    "find_detached_marker",
]
