from __future__ import annotations

"""Capability based routing for completion delivery.

The gate is deliberately conservative: an App Server protocol method existing
is insufficient evidence that it can target the user's active Desktop thread
with the same execution and approval semantics.
"""

import json
from pathlib import Path
from typing import Any, Dict


VALID_STATUSES = {"PASS", "PARTIAL", "FAIL", "UNKNOWN"}


def load_capabilities(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("capability result must be a JSON object")
    return value


def choose_architecture(payload: Dict[str, Any]) -> str:
    capabilities = {
        item.get("name"): item
        for item in payload.get("capabilities", [])
        if isinstance(item, dict) and item.get("name")
    }
    resume = capabilities.get("codex_exec_resume", {})
    observations = resume.get("observations", {}) or {}
    desktop_same_thread = observations.get("desktop_ui_same_thread") is True
    sandbox_restored = observations.get("sandbox_policy_restored") is True
    approval_restored = observations.get("approval_policy_restored") is True
    busy_delivery = capabilities.get("busy_thread_delivery", {}).get("status") == "PASS"
    turn_interrupt = capabilities.get("turn_interrupt", {}).get("status") == "PASS"
    direct_survival = capabilities.get("snooze_turn_interrupt_survival", {}).get("status") == "PASS"
    if (
        desktop_same_thread
        and sandbox_restored
        and approval_restored
        and busy_delivery
        and turn_interrupt
        and direct_survival
    ):
        return "APP_SERVER_DIRECT"
    if resume.get("status") == "PASS" and observations.get("conversation_history_restored") is True:
        return "CLI_RESUME_FALLBACK"
    return "MANUAL_DELIVERY"


def choose_control_plane(feature_flags: Dict[str, str]) -> str:
    """Choose the v0.2 control-plane mode from measured feature statuses.

    App Server method existence is intentionally insufficient for the direct
    mode.  The direct path requires the same-thread, policy, busy-thread and
    post-interrupt evidence to be PASS.  A usable App Server with an
    unproven safety edge is reported as APP_SERVER_PARTIAL so callers can keep
    the explicit CLI/manual path enabled without silently enabling automation.
    """
    flags = {str(key): str(value) for key, value in feature_flags.items()}
    direct_required = (
        "app_server_control",
        "desktop_ui_integration",
        "sandbox_preserved",
        "approval_preserved",
        "turn_interrupt",
        "busy_thread_delivery",
        "job_survives_interrupt",
        "automatic_completion_delivery",
    )
    if all(flags.get(name) == "PASS" for name in direct_required):
        return "APP_SERVER_DIRECT"
    if (
        flags.get("app_server_control") == "PASS"
        and flags.get("turn_interrupt") in {"PASS", "PARTIAL"}
    ):
        return "APP_SERVER_PARTIAL"
    if (
        flags.get("conversation_history_shared") == "PASS"
        and flags.get("cli_resume") == "PASS"
    ):
        return "CLI_RESUME_FALLBACK"
    return "MANUAL_DELIVERY"


__all__ = ["choose_architecture", "choose_control_plane", "load_capabilities"]
