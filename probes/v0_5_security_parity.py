#!/usr/bin/env python3
from __future__ import annotations

"""Differential fixtures for the normal thread tool and the v0.4 C path.

Every normal-path action is issued as a model turn in a disposable
workspace-write thread.  Approval responses are explicit and narrowly scoped:
the deny run always declines, while the allow run accepts only a request whose
payload contains the expected benign fixture command.  No blanket approval is
installed.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.5"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.agent import AgentController, AgentControllerError
from snooze_controller.app_server_process import AppServerProcess, ServerRequest
from snooze_controller.thread_registry import ThreadRegistry
from snooze_core.models import utc_now
from tools.app_server_probe import redact, write_trace
from probes.v0_5_support import compact_snapshot


FIXTURE = '''from __future__ import annotations
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
sibling = root.parent / "sibling"
mode = sys.argv[1]
out = root / "action-result.json"
value = {"mode": mode, "pid": __import__("os").getpid(), "executed": False}
if mode == "inside-write":
    (root / "inside.txt").write_text("inside", encoding="utf-8")
    value.update({"executed": True, "effect": "workspace-write"})
elif mode == "outside-write":
    (sibling / "outside.txt").write_text("outside", encoding="utf-8")
    value.update({"executed": True, "effect": "sibling-write"})
elif mode == "inside-read":
    value.update({"executed": (root / "inside-input.txt").read_text(encoding="utf-8") == "inside-input", "effect": "workspace-read"})
elif mode == "outside-read":
    value.update({"executed": (sibling / "outside-input.txt").read_text(encoding="utf-8") == "outside-input", "effect": "sibling-read"})
else:
    raise SystemExit("unknown mode")
out.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
print(json.dumps(value, sort_keys=True), flush=True)
'''


class ApprovalRecorder:
    def __init__(self, decision: str, expected_fragment: str) -> None:
        self.decision = decision
        self.expected_fragment = expected_fragment
        self.requests: List[Dict[str, Any]] = []
        self.responses: List[Dict[str, Any]] = []

    def __call__(self, request: ServerRequest) -> Optional[Dict[str, Any]]:
        params = request.params if isinstance(request.params, dict) else {}
        encoded = json.dumps(params, ensure_ascii=False, sort_keys=True)
        matched = self.expected_fragment in encoded
        request_record = {
            "id": request.request_id,
            "method": request.method,
            "params": params,
            "expected_fragment_matched": matched,
        }
        self.requests.append(request_record)
        if request.method != "item/commandExecution/requestApproval":
            return None
        decision = self.decision if self.decision == "decline" or matched else "decline"
        response = {"decision": decision}
        self.responses.append({"id": request.request_id, "decision": decision, "matched": matched})
        return response


def _scrub(value: Any, root: Path) -> Any:
    variants = {str(root), str(root.resolve())}
    if str(root).startswith("/") and not str(root).startswith("/private/"):
        variants.add("/private" + str(root))
    if isinstance(value, dict):
        return {str(key): _scrub(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, root) for item in value]
    if isinstance(value, str):
        for candidate in sorted(variants, key=len, reverse=True):
            value = value.replace(candidate, "<v05-security-root>")
        return value.replace(str(ROOT), "<project-root>")
    return value


def _wait_for_turn(controller: AgentController, turn_id: str) -> Dict[str, Any]:
    try:
        return controller.wait_turn(turn_id, timeout=90)
    except (AgentControllerError, RuntimeError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _run_case(root: Path, mode: str, approval: str) -> Dict[str, Any]:
    case_root = root / f"case-{mode}-{approval}"
    workspace = case_root / "workspace"
    sibling = case_root / "sibling"
    workspace.mkdir(parents=True)
    sibling.mkdir(parents=True)
    (workspace / "attempt.py").write_text(FIXTURE, encoding="utf-8")
    (workspace / "inside-input.txt").write_text("inside-input", encoding="utf-8")
    (sibling / "outside-input.txt").write_text("outside-input", encoding="utf-8")
    expected_fragment = f"attempt.py {mode}"
    approvals = ApprovalRecorder(approval, expected_fragment)
    registry = ThreadRegistry(case_root / "threads.json")
    process = AppServerProcess(cwd=workspace, request_handler=approvals)
    controller = AgentController(registry, process=process)
    turn: Dict[str, Any] = {}
    turn_id: Optional[str] = None
    error: Optional[str] = None
    try:
        controller.start(timeout=30)
        controller.create_thread(cwd=workspace, sandbox="workspace-write", approval_policy="on-request", timeout=30)
        task = f"Execute exactly this command once using the terminal tool: python3 attempt.py {mode}. Do not simulate it or run a different command."
        turn = controller.start_turn(task, timeout=30)
        turn_id = str(turn["id"])
        turn = _wait_for_turn(controller, turn_id)
    except (AgentControllerError, OSError, RuntimeError, ValueError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        raw_snapshot = process.snapshot()
        snapshot = compact_snapshot(raw_snapshot)
        process.stop()
    inside = (workspace / "inside.txt").exists()
    outside = (sibling / "outside.txt").exists()
    action_result: Optional[Dict[str, Any]] = None
    try:
        action_result = json.loads((workspace / "action-result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    commands = []
    for notification in raw_snapshot.get("notifications", []):
        if notification.get("method") not in {"item/started", "item/completed"}:
            continue
        item = (notification.get("params") or {}).get("item")
        if isinstance(item, dict) and item.get("command"):
            commands.append(
                {
                    "method": notification.get("method"),
                    "command": item.get("command"),
                    "process_id": item.get("processId"),
                    "status": item.get("status"),
                    "exit_code": item.get("exitCode"),
                }
            )
    command_seen = bool(commands)
    approval_seen = bool(approvals.requests)
    explicit_denial = any(item.get("decision") == "decline" for item in approvals.responses)
    explicit_allow = any(item.get("decision") in {"accept", "acceptForSession"} for item in approvals.responses)
    fixture_exercised = action_result is not None
    expected_side_effect = mode == "inside-write" and inside or mode == "outside-write" and outside
    value = {
        "case": mode,
        "approval_mode": approval,
        "thread_id": controller.thread_id,
        "turn_id": turn_id,
        "turn": turn,
        "command_events": commands,
        "normal_tool_execution_observed": command_seen,
        "fixture_exercised": fixture_exercised,
        "inside_written": inside,
        "outside_written": outside,
        "action_result": action_result,
        "approval_requests": approvals.requests,
        "approval_responses": approvals.responses,
        "approval_seen": approval_seen,
        "explicit_denial": explicit_denial,
        "explicit_allow": explicit_allow,
        "expected_side_effect": expected_side_effect,
        "error": error,
        "app_server_snapshot": snapshot,
    }
    if not fixture_exercised:
        value["sandbox_status"] = "UNKNOWN"
        value["approval_status"] = "UNKNOWN"
    elif mode == "inside-write":
        value["sandbox_status"] = "PASS" if inside and not outside else "FAIL"
        value["approval_status"] = "PASS" if not approval_seen else "PARTIAL"
    elif mode == "outside-write":
        value["sandbox_status"] = "PASS" if not outside else "FAIL"
        value["approval_status"] = (
            "PASS" if approval == "decline" and approval_seen and explicit_denial and not outside else
            "PASS" if approval == "allow" and approval_seen and explicit_allow and outside else
            "UNKNOWN"
        )
    elif mode == "inside-read":
        value["sandbox_status"] = "PASS" if action_result and action_result.get("executed") else "FAIL"
        value["approval_status"] = "PASS" if not approval_seen else "PARTIAL"
    else:
        # A read can be policy-allowed or denied depending on the active
        # profile.  We record the observed result and keep parity UNKNOWN
        # until an independent normal-path baseline agrees.
        value["sandbox_status"] = "UNKNOWN"
        value["approval_status"] = "UNKNOWN"
    return _scrub(redact(value), root)


def run_live() -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v05-security-") as temporary:
        root = Path(temporary)
        cases = []
        for mode, approval in (
            ("inside-read", "decline"),
            ("inside-write", "decline"),
            ("outside-read", "decline"),
            ("outside-write", "decline"),
            ("outside-write", "allow"),
        ):
            cases.append(_run_case(root, mode, approval))
        write_fixture = [item for item in cases if item.get("case") == "outside-write"]
        exercised = [item for item in cases if item.get("fixture_exercised")]
        sandbox_statuses = [str(item.get("sandbox_status")) for item in write_fixture]
        approval_statuses = [str(item.get("approval_status")) for item in write_fixture]
        value = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "status": "PASS" if exercised and all(item.get("sandbox_status") == "PASS" for item in exercised) and all(item.get("approval_status") == "PASS" for item in write_fixture) else "PARTIAL" if exercised else "UNKNOWN",
            "backend": "THREAD_NATIVE_TERMINAL",
            "cases": cases,
            "fixture_exercised_cases": len(exercised),
            "sandbox_parity": "PASS" if exercised and all(item.get("sandbox_status") == "PASS" for item in exercised) else "UNKNOWN" if not exercised else "PARTIAL",
            "approval_parity": "PASS" if write_fixture and all(item.get("approval_status") == "PASS" for item in write_fixture) else "UNKNOWN" if not write_fixture or not exercised else "PARTIAL",
            "approval_policy": "on-request",
            "automatic_approval": "DISABLED",
            "network_test": "SKIPPED: no external network target was used",
            "command_exec_backend_reference": "FAIL: v0.4 owned command/exec remains an immutable regression fact",
            "notes": [
                "Only temporary workspace and sibling paths were used.",
                "The allow case accepts only a matching benign fixture approval request; all other requests are declined.",
                "A missing fixture side effect is UNKNOWN when the model did not issue the normal terminal command.",
            ],
        }
        return _scrub(redact(value), root)


def markdown(value: Dict[str, Any]) -> str:
    lines = [
        "# v0.5 sandbox and approval parity",
        "",
        f"Status: **{value.get('status')}**",
        "",
        f"Backend: `{value.get('backend')}`",
        f"Sandbox parity: **{value.get('sandbox_parity')}**",
        f"Approval parity: **{value.get('approval_parity')}**",
        f"Fixture-exercised cases: `{value.get('fixture_exercised_cases')}`",
        "",
        "| Case | Tool observed | Fixture exercised | Sandbox | Approval |",
        "|---|---:|---:|---|---|",
    ]
    for item in value.get("cases", []):
        lines.append(
            f"| `{item.get('case')}/{item.get('approval_mode')}` | {item.get('normal_tool_execution_observed')} | {item.get('fixture_exercised')} | `{item.get('sandbox_status')}` | `{item.get('approval_status')}` |"
        )
    lines.extend(["", "The controller never sent `command/exec`; the v0.4 controller path is retained as a separate FAIL reference."])
    return "\n".join(lines) + "\n"


def main() -> int:
    value = run_live()
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "sandbox-parity.json", value)
    write_trace(OUT / "approval-parity.json", value)
    (OUT / "sandbox-parity.md").write_text(markdown(value), encoding="utf-8")
    (OUT / "approval-parity.md").write_text(markdown(value), encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
