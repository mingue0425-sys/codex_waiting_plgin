from __future__ import annotations

"""Snooze-owned App Server agent lifecycle."""

import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .app_server_process import AppServerLifecycle, AppServerProcess, AppServerProcessError
from .thread_registry import ThreadRegistry


class AgentControllerError(RuntimeError):
    pass


class AgentController:
    """Own a persistent App Server connection across multiple turns."""

    def __init__(
        self,
        registry: ThreadRegistry,
        *,
        command: Sequence[str] = ("codex", "app-server", "--stdio"),
        cwd: Optional[Path] = None,
        event_trace: Optional[Path] = None,
        process: Optional[AppServerProcess] = None,
    ) -> None:
        self.registry = registry
        self.event_trace = Path(event_trace) if event_trace is not None else None
        self.process = process or AppServerProcess(command, cwd=cwd)
        self.thread_id: Optional[str] = None
        self.last_turn_id: Optional[str] = None

    def start(self, *, timeout: float = 20.0) -> Dict[str, Any]:
        return self.process.start(timeout=timeout)

    def create_thread(
        self,
        *,
        cwd: Path,
        sandbox: Optional[str] = "workspace-write",
        approval_policy: Optional[Any] = "on-request",
        model: Optional[str] = None,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        if self.process.state != AppServerLifecycle.READY:
            self.start(timeout=timeout)
        params: Dict[str, Any] = {"cwd": str(Path(cwd).resolve()), "ephemeral": False}
        if sandbox is not None:
            params["sandbox"] = sandbox
        if approval_policy is not None:
            params["approvalPolicy"] = approval_policy
        if model:
            params["model"] = model
        response = self.process.request("thread/start", params, timeout=timeout)
        if response.get("error") is not None:
            raise AgentControllerError(f"thread/start failed: {response.get('error')}")
        result = response.get("result") or {}
        thread = result.get("thread") or {}
        thread_id = thread.get("id")
        if not thread_id:
            raise AgentControllerError("thread/start did not return a thread id")
        self.thread_id = str(thread_id)
        self.registry.register(
            self.thread_id,
            app_server_instance=self.process.instance_id or "unknown",
            cwd=Path(cwd),
            sandbox=sandbox,
            approval_policy=approval_policy,
            model=result.get("model") or model,
        )
        return result

    def resume_thread(self, thread_id: str, *, timeout: float = 30.0, cwd: Optional[Path] = None) -> Dict[str, Any]:
        if self.process.state != AppServerLifecycle.READY:
            self.start(timeout=timeout)
        record = self.registry.get(thread_id)
        params: Dict[str, Any] = {"threadId": thread_id, "excludeTurns": True}
        if cwd is not None:
            params["cwd"] = str(Path(cwd).resolve())
        elif record and record.get("cwd"):
            params["cwd"] = record["cwd"]
        response = self.process.request("thread/resume", params, timeout=timeout)
        if response.get("error") is not None:
            raise AgentControllerError(f"thread/resume failed: {response.get('error')}")
        self.thread_id = thread_id
        if record is not None:
            self.registry.mark_state(thread_id, "LIVE", app_server_instance=self.process.instance_id)
        return response.get("result") or {}

    def start_turn(
        self,
        text: str,
        *,
        thread_id: Optional[str] = None,
        timeout: float = 30.0,
        cwd: Optional[Path] = None,
        approval_policy: Optional[Any] = None,
        sandbox_policy: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        target = thread_id or self.thread_id
        if not target:
            raise AgentControllerError("thread id is not set")
        if not text:
            raise AgentControllerError("turn text cannot be empty")
        params: Dict[str, Any] = {
            "threadId": target,
            "input": [{"type": "text", "text": text, "text_elements": []}],
            "clientUserMessageId": str(uuid.uuid4()),
        }
        if cwd is not None:
            params["cwd"] = str(Path(cwd).resolve())
        if approval_policy is not None:
            params["approvalPolicy"] = approval_policy
        if sandbox_policy is not None:
            params["sandboxPolicy"] = sandbox_policy
        response = self.process.request("turn/start", params, timeout=timeout)
        if response.get("error") is not None:
            raise AgentControllerError(f"turn/start failed: {response.get('error')}")
        turn = (response.get("result") or {}).get("turn") or {}
        turn_id = turn.get("id")
        if not turn_id:
            raise AgentControllerError("turn/start did not return a turn id")
        self.thread_id = target
        self.last_turn_id = str(turn_id)
        if self.registry.get(target) is not None:
            self.registry.append_turn(target, self.last_turn_id)
        return turn

    def wait_turn(self, turn_id: Optional[str] = None, *, timeout: float = 300.0) -> Dict[str, Any]:
        target = turn_id or self.last_turn_id
        if not target:
            raise AgentControllerError("turn id is not set")
        notification = self.process.wait_for_notification(
            lambda item: item.get("method") == "turn/completed"
            and ((item.get("params") or {}).get("turn") or {}).get("id") == target,
            timeout=timeout,
        )
        if notification is None:
            raise AgentControllerError(f"turn did not complete before timeout: {target}")
        turn = ((notification.get("params") or {}).get("turn") or {})
        if self.thread_id and self.registry.get(self.thread_id) is not None:
            self.registry.update_turn(self.thread_id, target, status=turn.get("status"), completed_at=notification.get("received_at"))
        return turn

    def continue_turn(self, text: str, *, timeout: float = 30.0, wait_timeout: float = 300.0) -> Dict[str, Any]:
        self.start_turn(text, timeout=timeout)
        return self.wait_turn(timeout=wait_timeout)

    def background_terminals(self, *, thread_id: Optional[str] = None, timeout: float = 30.0) -> Dict[str, Any]:
        target = thread_id or self.thread_id
        if not target:
            raise AgentControllerError("thread id is not set")
        response = self.process.request(
            "thread/backgroundTerminals/list", {"threadId": target}, timeout=timeout
        )
        if response.get("error") is not None:
            raise AgentControllerError(f"background terminal list failed: {response.get('error')}")
        return response.get("result") or {}

    def snapshot(self) -> Dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "last_turn_id": self.last_turn_id,
            "registry": self.registry.get(self.thread_id) if self.thread_id else None,
            "app_server": self.process.snapshot(),
        }

    def run(
        self,
        task: str,
        *,
        cwd: Path,
        sandbox: Optional[str] = "workspace-write",
        approval_policy: Optional[Any] = "on-request",
        model: Optional[str] = None,
        continuation: Optional[str] = None,
        start_timeout: float = 20.0,
        request_timeout: float = 30.0,
        turn_timeout: float = 300.0,
    ) -> Dict[str, Any]:
        try:
            self.start(timeout=start_timeout)
            self.create_thread(
                cwd=cwd,
                sandbox=sandbox,
                approval_policy=approval_policy,
                model=model,
                timeout=request_timeout,
            )
            first_turn = self.start_turn(task, timeout=request_timeout)
            first_done = self.wait_turn(first_turn.get("id"), timeout=turn_timeout)
            continuation_done: Optional[Dict[str, Any]] = None
            if continuation:
                second_turn = self.start_turn(continuation, timeout=request_timeout)
                continuation_done = self.wait_turn(second_turn.get("id"), timeout=turn_timeout)
            return {
                "control_plane": "SNOOZE_APP_SERVER",
                "thread_id": self.thread_id,
                "turns": [first_done] + ([continuation_done] if continuation_done else []),
                "server_requests": [
                    {"id": item.request_id, "method": item.method, "params": item.params}
                    for item in self.process.server_requests
                ],
                "event_count": len(self.process.events),
                "notification_count": len(self.process.notifications),
                "token_usage_events": [
                    item for item in self.process.notifications if item.get("method") == "thread/tokenUsage/updated"
                ],
                "app_server_state": self.process.state.value,
            }
        finally:
            self.process.stop()
            if self.event_trace is not None:
                self.process.write_trace(self.event_trace)

    def run_existing(
        self,
        thread_id: str,
        task: str,
        *,
        cwd: Optional[Path] = None,
        continuation: Optional[str] = None,
        start_timeout: float = 20.0,
        request_timeout: float = 30.0,
        turn_timeout: float = 300.0,
    ) -> Dict[str, Any]:
        """Resume a registry-owned durable thread and run explicit turns."""
        try:
            self.start(timeout=start_timeout)
            self.resume_thread(thread_id, timeout=request_timeout, cwd=cwd)
            first_turn = self.start_turn(task, timeout=request_timeout)
            first_done = self.wait_turn(first_turn.get("id"), timeout=turn_timeout)
            continuation_done: Optional[Dict[str, Any]] = None
            if continuation:
                second_turn = self.start_turn(continuation, timeout=request_timeout)
                continuation_done = self.wait_turn(second_turn.get("id"), timeout=turn_timeout)
            return {
                "control_plane": "SNOOZE_APP_SERVER",
                "resumed": True,
                "thread_id": self.thread_id,
                "turns": [first_done] + ([continuation_done] if continuation_done else []),
                "server_requests": [
                    {"id": item.request_id, "method": item.method, "params": item.params}
                    for item in self.process.server_requests
                ],
                "event_count": len(self.process.events),
                "notification_count": len(self.process.notifications),
                "token_usage_events": [
                    item for item in self.process.notifications if item.get("method") == "thread/tokenUsage/updated"
                ],
                "app_server_state": self.process.state.value,
            }
        finally:
            self.process.stop()
            if self.event_trace is not None:
                self.process.write_trace(self.event_trace)


__all__ = ["AgentController", "AgentControllerError"]
