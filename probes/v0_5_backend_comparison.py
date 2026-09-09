#!/usr/bin/env python3
from __future__ import annotations

"""Build the v0.5 backend comparison and production selector evidence."""

import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.5"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.v05_backend import V05Backend, select_backend
from snooze_core.models import utc_now
from tools.app_server_probe import redact, write_trace


VALID = {"PASS", "PARTIAL", "FAIL", "UNKNOWN"}


def load(name: str) -> Dict[str, Any]:
    path = OUT / name
    if not path.exists():
        return {"status": "UNKNOWN", "missing": name}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {"status": "UNKNOWN", "invalid": name}
    except (OSError, ValueError):
        return {"status": "UNKNOWN", "invalid": name}


def valid(value: Any) -> str:
    status = str(value)
    return status if status in VALID else "UNKNOWN"


def build() -> Dict[str, Any]:
    native = load("native-terminal-e2e.json")
    security = load("sandbox-parity.json")
    inheritance = load("process-inheritance.json")
    schema = load("native-terminal-schema.json")
    c_security = load("../v0.4/security-parity.json")
    # The v0.4 path is an immutable reference.  Even if an old artifact is
    # missing, the v0.5 selector must not silently treat it as safe.
    c_sandbox = "FAIL"
    c_approval = "FAIL"
    if c_security.get("owned_path_wider_than_workspace") is True:
        c_sandbox = "FAIL"
        c_approval = "FAIL" if c_security.get("approval_parity") == "FAIL" else "UNKNOWN"
    a_sandbox = valid(security.get("sandbox_parity"))
    a_approval = valid(security.get("approval_parity"))
    a_integrity = valid(native.get("command_integrity"))
    a_required = {
        "status": "PASS"
        if all(
            [
                valid(native.get("status")) == "PASS",
                a_sandbox == "PASS",
                a_approval == "PASS",
                valid(native.get("handoff_10s")) == "PASS",
                valid(native.get("job_survival")) == "PASS",
                valid(native.get("model_idle")) == "PASS",
                valid(native.get("auto_continuation")) == "PASS",
                a_integrity == "PASS",
            ]
        )
        else "PARTIAL" if native.get("normal_tool_execution_observed") else "UNKNOWN",
        "sandbox_parity": a_sandbox,
        "approval_parity": a_approval,
        "handoff_10s": valid(native.get("handoff_10s")),
        "job_survival": valid(native.get("job_survival")),
        "model_idle": valid(native.get("model_idle")),
        "auto_continuation": valid(native.get("auto_continuation")),
        "command_integrity": a_integrity,
    }
    b_sandbox = valid(inheritance.get("sandbox_inheritance"))
    b_approval = valid(inheritance.get("approval_integrity"))
    b_required = {
        "status": "PASS"
        if all(
            [
                valid(inheritance.get("status")) == "PASS",
                b_sandbox == "PASS",
                b_approval == "PASS",
                valid(inheritance.get("handoff_10s")) == "PASS",
                valid(inheritance.get("job_survival")) == "PASS",
                valid(inheritance.get("model_idle")) == "PASS",
                valid(inheritance.get("auto_continuation")) == "PASS",
                valid(inheritance.get("command_integrity")) == "PASS",
            ]
        )
        else "PARTIAL" if inheritance.get("fixture_exercised") else "UNKNOWN",
        "sandbox_parity": b_sandbox,
        "approval_parity": b_approval,
        "handoff_10s": valid(inheritance.get("handoff_10s")),
        "job_survival": valid(inheritance.get("job_survival")),
        "model_idle": valid(inheritance.get("model_idle")),
        "auto_continuation": valid(inheritance.get("auto_continuation")),
        "command_integrity": valid(inheritance.get("command_integrity")),
    }
    evidence = {
        "THREAD_NATIVE_TERMINAL": a_required,
        "SANDBOX_DESCENDANT_SUPERVISOR": b_required,
    }
    selected = select_backend(evidence)
    value = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "backend_matrix": {
            "THREAD_NATIVE_TERMINAL": {
                **a_required,
                "evidence": ["native-terminal-schema.json", "native-terminal-e2e.json", "sandbox-parity.json", "approval-parity.json"],
            },
            "SANDBOX_DESCENDANT_SUPERVISOR": {
                **b_required,
                "evidence": ["process-inheritance.json", "sandbox-parity.json", "approval-parity.json"],
            },
            "CONTROLLER_COMMAND_EXEC": {
                "status": "FAIL",
                "sandbox_parity": c_sandbox,
                "approval_parity": c_approval,
                "command_integrity": "PASS",
                "reason": "immutable v0.4 fixture showed a sibling write through the controller-level command/exec path",
                "evidence": ["../v0.4/security-parity.json"],
            },
        },
        "schema_status": valid(schema.get("status")),
        "selected_backend": selected.value,
        "production_path": "CLI_RESUME_FALLBACK",
        "production_automation": "DISABLED_SECURITY_GATE",
        "experimental_backends": ["THREAD_NATIVE_TERMINAL", "SANDBOX_DESCENDANT_SUPERVISOR"],
        "automatic_features_enabled": [],
        "exactly_once": "NOT_CLAIMED",
        "notes": [
            "Backend C remains a regression/reference failure and is never selected.",
            "A or B requires every execution, security, integrity, handoff and delivery field to be PASS.",
            "UNKNOWN and PARTIAL evidence select CLI_RESUME_FALLBACK.",
        ],
    }
    return redact(value)


def markdown(value: Dict[str, Any]) -> str:
    lines = [
        "# v0.5 backend comparison",
        "",
        f"Selected backend: **{value['selected_backend']}**",
        f"Production path: **{value['production_path']}**",
        f"Automation: **{value['production_automation']}**",
        "",
        "| Backend | Status | Sandbox | Approval | Integrity |",
        "|---|---|---|---|---|",
    ]
    for name, item in value["backend_matrix"].items():
        lines.append(f"| `{name}` | **{item['status']}** | `{item['sandbox_parity']}` | `{item['approval_parity']}` | `{item['command_integrity']}` |")
    lines.extend(["", "No candidate with UNKNOWN or PARTIAL security evidence is automatically selected.", ""])
    lines.extend(f"- {note}" for note in value["notes"])
    return "\n".join(lines) + "\n"


def main() -> int:
    value = build()
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "backend-comparison.json", value)
    (OUT / "backend-comparison.md").write_text(markdown(value), encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
