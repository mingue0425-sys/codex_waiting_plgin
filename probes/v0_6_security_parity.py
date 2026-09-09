#!/usr/bin/env python3
from __future__ import annotations

"""Independent A/B sandbox and approval differential probe.

The target action is always requested from a normal Codex thread.  Backend B
starts its supervisor as a descendant of that normal command; the controller
never starts either the supervisor or the target action.
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.6"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from probes.v0_5_support import compact_snapshot
from snooze_controller.agent import AgentController, AgentControllerError
from snooze_controller.app_server_process import AppServerProcess
from snooze_controller.thread_registry import ThreadRegistry
from snooze_core.models import utc_now
from tools.app_server_probe import redact, write_trace
from probes.v0_6_support import ScopedApprovalRecorder, command_events


ACTION_FIXTURE = '''from __future__ import annotations
import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
sibling = root.parent / "sibling"
mode = sys.argv[1]
result = {"mode": mode, "pid": os.getpid(), "executed": False}
if mode == "inside-write":
    (root / "inside.txt").write_text("inside", encoding="utf-8")
    result.update(executed=True, effect="workspace-write")
elif mode == "outside-write":
    (sibling / "outside.txt").write_text("outside", encoding="utf-8")
    result.update(executed=True, effect="sibling-write")
elif mode == "outside-read":
    result.update(executed=(sibling / "outside-input.txt").read_text(encoding="utf-8") == "outside-input", effect="sibling-read")
else:
    raise SystemExit("unknown mode")
(root / "action-result.json").write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
print(json.dumps(result, sort_keys=True), flush=True)
'''


SUPERVISOR_FIXTURE = '''from __future__ import annotations
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
child = root / "child_action.py"
child.write_text({action!r}, encoding="utf-8")
raise SystemExit(subprocess.run([sys.executable, str(child), sys.argv[1]], cwd=root, check=False).returncode)
'''


def scrub(value: Any, root: Path) -> Any:
    variants = {str(root), str(root.resolve())}
    if str(root).startswith("/") and not str(root).startswith("/private/"):
        variants.add("/private" + str(root))
    if isinstance(value, dict):
        return {str(key): scrub(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item, root) for item in value]
    if isinstance(value, str):
        for candidate in sorted(variants, key=len, reverse=True):
            value = value.replace(candidate, "<v06-security-root>")
        return value.replace(str(ROOT), "<project-root>")
    return value


def read_action(workspace: Path) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads((workspace / "action-result.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def run_case(root: Path, candidate: str, mode: str, approval: str) -> Dict[str, Any]:
    case_root = root / f"{candidate}-{mode}-{approval}"
    workspace = case_root / "workspace"
    sibling = case_root / "sibling"
    workspace.mkdir(parents=True)
    sibling.mkdir(parents=True)
    action_path = workspace / "action_fixture.py"
    action_path.write_text(ACTION_FIXTURE, encoding="utf-8")
    (sibling / "outside-input.txt").write_text("outside-input", encoding="utf-8")
    if candidate == "THREAD_NATIVE_TERMINAL":
        command = f"python3 action_fixture.py {mode}"
        expected_fragment = f"action_fixture.py {mode}"
    else:
        supervisor = workspace / "supervisor_fixture.py"
        supervisor.write_text(SUPERVISOR_FIXTURE.format(action=ACTION_FIXTURE), encoding="utf-8")
        command = f"python3 supervisor_fixture.py {mode}"
        expected_fragment = f"supervisor_fixture.py {mode}"
    approvals = ScopedApprovalRecorder(approval, expected_fragment=expected_fragment)
    process = AppServerProcess(cwd=workspace, request_handler=approvals)
    controller = AgentController(ThreadRegistry(case_root / "threads.json"), process=process)
    turn: Dict[str, Any] = {}
    turn_id: Optional[str] = None
    error: Optional[str] = None
    try:
        controller.start(timeout=30)
        controller.create_thread(cwd=workspace, sandbox="workspace-write", approval_policy="on-request", timeout=30)
        turn = controller.start_turn(
            f"Use the normal terminal tool and execute exactly once: {command}. Do not simulate or replace it.",
            timeout=30,
        )
        turn_id = str(turn["id"])
        turn = controller.wait_turn(turn_id, timeout=90)
    except (AgentControllerError, OSError, RuntimeError, ValueError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    raw_snapshot = process.snapshot()
    process.stop()
    action = read_action(workspace)
    command_items = command_events(raw_snapshot.get("notifications", []))
    outside_written = (sibling / "outside.txt").exists()
    inside_written = (workspace / "inside.txt").exists()
    approval_requests = [item for item in approvals.requests if item.get("method") == "item/commandExecution/requestApproval"]
    decisions = [item.get("decision") for item in approval_requests]
    fixture_exercised = action is not None
    if not fixture_exercised:
        sandbox_status = "UNKNOWN"
        approval_status = "UNKNOWN"
    elif mode == "outside-write":
        sandbox_status = "FAIL" if outside_written else "PASS"
        if approval == "decline":
            approval_status = "PASS" if approval_requests and "decline" in decisions and not outside_written else "UNKNOWN"
        else:
            approval_status = "PASS" if approval_requests and decisions and decisions[-1] in {"accept", "acceptForSession"} and outside_written else "UNKNOWN"
    elif mode == "inside-write":
        sandbox_status = "PASS" if inside_written else "FAIL"
        approval_status = "UNKNOWN"
    else:
        sandbox_status = "UNKNOWN"
        approval_status = "UNKNOWN"
    return scrub(redact({
        "candidate": candidate,
        "case": mode,
        "approval_mode": approval,
        "command": command,
        "thread_id": controller.thread_id,
        "turn_id": turn_id,
        "turn": turn,
        "command_events": command_items,
        "normal_tool_execution_observed": bool(command_items),
        "fixture_exercised": fixture_exercised,
        "action_result": action,
        "inside_written": inside_written,
        "outside_written": outside_written,
        "approval_requests": approval_requests,
        "approval_seen": bool(approval_requests),
        "approval_decisions": decisions,
        "sandbox_status": sandbox_status,
        "approval_status": approval_status,
        "error": error,
        "controller_candidate_execution_used": False,
        "app_server_snapshot": compact_snapshot(raw_snapshot),
    }), root)


def aggregate(cases: List[Dict[str, Any]], candidate: str) -> Dict[str, Any]:
    selected = [item for item in cases if item.get("candidate") == candidate]
    exercised = [item for item in selected if item.get("fixture_exercised")]
    writes = [item for item in selected if item.get("case") == "outside-write"]
    sandbox = "UNKNOWN" if not exercised else "PASS" if all(item.get("sandbox_status") == "PASS" for item in exercised if item.get("case") != "outside-read") else "FAIL" if any(item.get("sandbox_status") == "FAIL" for item in exercised) else "UNKNOWN"
    approval = "PASS" if writes and all(item.get("approval_status") == "PASS" for item in writes) else "UNKNOWN" if not exercised or not writes else "FAIL" if any(item.get("approval_status") == "FAIL" for item in writes) else "UNKNOWN"
    return {
        "status": "PASS" if sandbox == approval == "PASS" else "UNKNOWN" if not exercised else "PARTIAL",
        "sandbox_parity": sandbox,
        "approval_parity": approval,
        "fixture_exercised_cases": len(exercised),
        "cases": selected,
    }


def run(runs_per_candidate: int = 1) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v06-security-") as temporary:
        root = Path(temporary)
        cases: List[Dict[str, Any]] = []
        for index in range(runs_per_candidate):
            for candidate in ("THREAD_NATIVE_TERMINAL", "SANDBOX_DESCENDANT_SUPERVISOR"):
                for mode, approval in (("inside-write", "decline"), ("outside-write", "decline"), ("outside-write", "accept"), ("outside-read", "decline")):
                    cases.append(run_case(root / f"run-{index + 1}", candidate, mode, approval))
        matrix = {candidate: aggregate(cases, candidate) for candidate in ("THREAD_NATIVE_TERMINAL", "SANDBOX_DESCENDANT_SUPERVISOR")}
        c_reference: Dict[str, Any] = {"status": "FAIL", "source": "results/v0.4/security-parity.json", "production_candidate": False}
        value = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "status": "PASS" if all(item.get("status") == "PASS" for item in matrix.values()) else "UNKNOWN",
            "runs_per_candidate": runs_per_candidate,
            "fixture_semantics": ["inside-write", "outside-write", "outside-read"],
            "backend_matrix": {
                "THREAD_NATIVE_TERMINAL": matrix["THREAD_NATIVE_TERMINAL"],
                "SANDBOX_DESCENDANT_SUPERVISOR": matrix["SANDBOX_DESCENDANT_SUPERVISOR"],
                "CONTROLLER_COMMAND_EXEC": c_reference,
            },
            "sandbox_parity": matrix["THREAD_NATIVE_TERMINAL"]["sandbox_parity"],
            "approval_parity": matrix["THREAD_NATIVE_TERMINAL"]["approval_parity"],
            "approval_policy": "on-request",
            "automatic_approval": "DISABLED",
            "controller_candidate_execution_used": False,
            "notes": [
                "All candidate actions were requested through normal Codex thread turns.",
                "Backend B's supervisor was created only by the requested normal command fixture.",
                "A missing fixture side effect or missing approval request is UNKNOWN.",
                "C is retained as a negative control and is never used as candidate evidence.",
            ],
        }
        return scrub(redact(value), root)


def markdown(value: Dict[str, Any]) -> str:
    lines = [
        "# v0.6 sandbox and approval parity",
        "",
        f"Aggregate status: **{value.get('status')}**",
        "",
        "| Backend | Sandbox | Approval | Fixture cases |",
        "|---|---|---|---:|",
    ]
    for name, item in value.get("backend_matrix", {}).items():
        lines.append(f"| `{name}` | `{item.get('sandbox_parity', item.get('status'))}` | `{item.get('approval_parity', item.get('status'))}` | `{item.get('fixture_exercised_cases', 'reference')}` |")
    lines.extend(["", "No candidate action was started by the controller. Approval is UNKNOWN without an observed request and decision.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-per-candidate", type=int, default=1)
    args = parser.parse_args()
    value = run(args.runs_per_candidate)
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "sandbox-parity.json", value)
    write_trace(OUT / "approval-parity.json", value)
    (OUT / "sandbox-parity.md").write_text(markdown(value), encoding="utf-8")
    (OUT / "approval-parity.md").write_text(markdown(value), encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
