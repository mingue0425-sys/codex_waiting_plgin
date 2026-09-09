from __future__ import annotations

"""Snooze-owned App Server agent lifecycle."""

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .app_server_process import AppServerLifecycle, AppServerProcess, AppServerProcessError
from .model_policy import (
    LUNA_MODEL,
    REASONING_EFFORT,
    ModelAttestation,
    ModelPolicyError,
    attest_model,
    first_model,
    luna_thread_params,
    luna_turn_params,
    models_from_events,
    subagent_events,
)
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
        self.requested_model = LUNA_MODEL
        self.reasoning_effort = REASONING_EFFORT
        self.runtime_reported_model: Optional[str] = None
        self.thread_model: Optional[str] = None
        self.turn_model: Optional[str] = None
        self._attestation_complete = False
        self._attestation_in_progress = False

    def start(self, *, timeout: float = 20.0) -> Dict[str, Any]:
        return self.process.start(timeout=timeout)

    def create_thread(
        self,
        *,
        cwd: Path,
        sandbox: Optional[str] = "workspace-write",
        approval_policy: Optional[Any] = "on-request",
        model: Optional[str] = None,
        developer_instructions: Optional[str] = None,
        dynamic_tools: Optional[list[Dict[str, Any]]] = None,
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        if self.process.state != AppServerLifecycle.READY:
            self.start(timeout=timeout)
        requested_model = model or self.requested_model
        if requested_model != LUNA_MODEL:
            raise ModelPolicyError(f"Codex Snooze only permits {LUNA_MODEL}; got {requested_model}")
        self.requested_model = requested_model
        self._attestation_complete = False
        params: Dict[str, Any] = {"cwd": str(Path(cwd).resolve()), "ephemeral": False}
        if sandbox is not None:
            params["sandbox"] = sandbox
        if approval_policy is not None:
            params["approvalPolicy"] = approval_policy
        if model:
            params["model"] = model
        if developer_instructions is not None:
            params["developerInstructions"] = developer_instructions
        if dynamic_tools is not None:
            params["dynamicTools"] = dynamic_tools
        params = luna_thread_params(params)
        response = self.process.request("thread/start", params, timeout=timeout)
        if response.get("error") is not None:
            raise AgentControllerError(f"thread/start failed: {response.get('error')}")
        result = response.get("result") or {}
        thread = result.get("thread") or {}
        thread_id = thread.get("id")
        if not thread_id:
            raise AgentControllerError("thread/start did not return a thread id")
        self.thread_id = str(thread_id)
        self.thread_model = first_model(thread, result)
        self.runtime_reported_model = first_model(result)
        self.registry.register(
            self.thread_id,
            app_server_instance=self.process.instance_id or "unknown",
            cwd=Path(cwd),
            sandbox=sandbox,
            approval_policy=approval_policy,
            model=self.thread_model or requested_model,
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
        resumed_result = response.get("result") or {}
        self.thread_model = first_model(resumed_result)
        self.runtime_reported_model = first_model(resumed_result)
        self.thread_id = thread_id
        if record is not None:
            self.registry.mark_state(thread_id, "LIVE", app_server_instance=self.process.instance_id)
        return resumed_result

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
        real_codex = bool(self.process.command and Path(self.process.command[0]).name == "codex")
        if real_codex and not self._attestation_complete and not self._attestation_in_progress:
            attestation = self.attest_luna_model(timeout=timeout, wait_timeout=max(timeout, 60.0))
            if not attestation.verified:
                raise ModelPolicyError("MODEL_ATTESTATION=FAIL: " + ",".join(attestation.reasons))
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
        params = luna_turn_params(params)
        response = self.process.request("turn/start", params, timeout=timeout)
        if response.get("error") is not None:
            raise AgentControllerError(f"turn/start failed: {response.get('error')}")
        turn = (response.get("result") or {}).get("turn") or {}
        turn_id = turn.get("id")
        if not turn_id:
            raise AgentControllerError("turn/start did not return a turn id")
        self.thread_id = target
        self.last_turn_id = str(turn_id)
        self.turn_model = first_model(turn, response.get("result"))
        if self.turn_model is None:
            self.turn_model = first_model(
                *(
                    notification.get("params")
                    for notification in self.process.notifications
                    if notification.get("method") in {"turn/started", "turn/completed"}
                )
            )
        if self.registry.get(target) is not None:
            self.registry.append_turn(target, self.last_turn_id)
        return turn

    def model_attestation(self) -> ModelAttestation:
        """Return strict model evidence accumulated by this App Server run."""

        observed_models = models_from_events(
            event
            for event in self.process.events
            if event.get("kind") in {"response", "notification"}
        )
        non_luna = sum(1 for value in observed_models if value != LUNA_MODEL)
        delegation = subagent_events(self.process.events)
        delegation_names = [str(item.get("method") or item.get("kind") or "unknown") for item in delegation]
        review_values: List[str] = []
        for event in self.process.events:
            text = str(event.get("params", ""))
            if "auto_review" in text or "guardian_subagent" in text:
                review_values.append("auto_review")
        return attest_model(
            requested_model=self.requested_model,
            runtime_reported_model=self.runtime_reported_model,
            thread_model=self.thread_model,
            turn_model=self.turn_model,
            reasoning_effort=self.reasoning_effort,
            fallback_detected=False,
            non_luna_model_calls=non_luna,
            auto_review_model_override=review_values[0] if review_values else None,
            review_model="user" if not review_values else review_values[0],
            subagent_calls=delegation_names,
        )

    def attest_luna_model(self, *, timeout: float = 30.0, wait_timeout: float = 60.0) -> ModelAttestation:
        """Run a no-tool attestation turn before any capability task."""

        if not self.thread_id:
            raise AgentControllerError("thread id is not set")
        self._attestation_in_progress = True
        try:
            attestation_turn = self.start_turn(
                "Model attestation only. Do not use tools or delegate. Reply exactly MODEL_ATTESTATION_READY.",
                timeout=timeout,
            )
            self.wait_turn(str(attestation_turn["id"]), timeout=wait_timeout)
            attestation = self.model_attestation()
            self._attestation_complete = attestation.verified
            return attestation
        finally:
            self._attestation_in_progress = False

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
        # Some runtimes expose the selected model only on the terminal turn
        # notification. Retain that evidence before evaluating the mandatory
        # attestation; a missing field still fails closed in attest_model.
        self.turn_model = first_model(turn, notification) or self.turn_model
        self.runtime_reported_model = first_model(notification) or self.runtime_reported_model
        if self.thread_id and self.registry.get(self.thread_id) is not None:
            self.registry.update_turn(self.thread_id, target, status=turn.get("status"), completed_at=notification.get("received_at"))
        return turn

    def completed_turn(self, turn_id: str) -> Optional[Dict[str, Any]]:
        """Return the terminal turn payload already observed, if any."""
        for item in reversed(self.process.notifications):
            if item.get("method") != "turn/completed":
                continue
            turn = ((item.get("params") or {}).get("turn") or {})
            if str(turn.get("id")) == str(turn_id):
                return dict(turn)
        return None

    def interrupt_turn(self, turn_id: Optional[str] = None, *, timeout: float = 30.0) -> Dict[str, Any]:
        target = turn_id or self.last_turn_id
        if not target or not self.thread_id:
            raise AgentControllerError("thread and turn ids are required to interrupt")
        response = self.process.request(
            "turn/interrupt",
            {"threadId": self.thread_id, "turnId": target},
            timeout=timeout,
        )
        if response.get("error") is not None:
            raise AgentControllerError(f"turn/interrupt failed: {response.get('error')}")
        return response.get("result") or {}

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
            "model_attestation": self.model_attestation().as_dict(),
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
            attestation = self.attest_luna_model(timeout=request_timeout, wait_timeout=turn_timeout)
            if not attestation.verified:
                raise ModelPolicyError("MODEL_ATTESTATION=FAIL: " + ",".join(attestation.reasons))
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
                "model_attestation": attestation.as_dict(experiment_valid=True),
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
            attestation = self.attest_luna_model(timeout=request_timeout, wait_timeout=turn_timeout)
            if not attestation.verified:
                raise ModelPolicyError("MODEL_ATTESTATION=FAIL: " + ",".join(attestation.reasons))
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
                "model_attestation": attestation.as_dict(experiment_valid=True),
            }
        finally:
            self.process.stop()
            if self.event_trace is not None:
                self.process.write_trace(self.event_trace)


__all__ = ["AgentController", "AgentControllerError"]
