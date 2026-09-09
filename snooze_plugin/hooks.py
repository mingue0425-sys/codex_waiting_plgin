from __future__ import annotations

from typing import Any, Dict


def pre_tool_use(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return a transparent decision until sandbox/thread identity is proven."""
    return {
        "action": "PASS_THROUGH",
        "feature_gate": "AUTOMATIC_INTERCEPTION_DISABLED",
        "reason": "Desktop thread and sandbox/approval preservation are UNKNOWN",
        "input_keys": sorted(payload.keys()),
    }
