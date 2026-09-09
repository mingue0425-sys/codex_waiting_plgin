#!/usr/bin/env python3
from __future__ import annotations

"""Probe descendant inheritance from a normal sandboxed terminal command.

The only evidence accepted for Backend B comes from a command actually issued
by the normal Codex thread tool.  A host-side subprocess experiment is kept
out of the capability result because it cannot establish a Codex sandbox
boundary.
"""

import json
import os
import subprocess
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
import os
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parent
sibling = root.parent / "sibling"
role = sys.argv[1]
record = root / f"{role}.json"
value = {"role": role, "pid": os.getpid(), "ppid": os.getppid(), "sid": os.getsid(0), "outside_written": False}
if role == "parent":
    child = subprocess.Popen([sys.executable, __file__, "child"], cwd=root)
    value["child_pid"] = child.pid
    child.wait(timeout=10)
    value["child_returncode"] = child.returncode
    value["parent_exited_before_descendant"] = (root / "detached.json").exists()
elif role == "child":
    grandchild = subprocess.Popen([sys.executable, __file__, "grandchild"], cwd=root, start_new_session=True)
    value["grandchild_pid"] = grandchild.pid
    (root / "child-started.json").write_text(json.dumps(value), encoding="utf-8")
    raise SystemExit(0)
elif role == "grandchild":
    time.sleep(0.6)
    try:
        (sibling / "detached-outside.txt").write_text("descendant", encoding="utf-8")
        value["outside_written"] = True
    except OSError as exc:
        value["write_error"] = type(exc).__name__
    (root / "detached.json").write_text(json.dumps(value), encoding="utf-8")
else:
    raise SystemExit("bad role")
record.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
print(json.dumps(value, sort_keys=True), flush=True)
'''


class DecliningApprovals:
    def __init__(self) -> None:
        self.requests: List[Dict[str, Any]] = []

    def __call__(self, request: ServerRequest) -> Optional[Dict[str, Any]]:
        params = request.params if isinstance(request.params, dict) else {}
        self.requests.append({"id": request.request_id, "method": request.method, "params": params})
        if request.method == "item/commandExecution/requestApproval":
            return {"decision": "decline"}
        return None


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
            value = value.replace(candidate, "<v05-inheritance-root>")
        return value.replace(str(ROOT), "<project-root>")
    return value


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _run_host_reference(root: Path) -> Dict[str, Any]:
    host_root = root / "host-reference"
    workspace = host_root / "workspace"
    sibling = host_root / "sibling"
    host_root.mkdir()
    workspace.mkdir()
    sibling.mkdir()
    fixture = workspace / "inheritance_fixture.py"
    fixture.write_text(FIXTURE, encoding="utf-8")
    completed = subprocess.run([sys.executable, str(fixture), "parent"], cwd=workspace, capture_output=True, text=True, timeout=20, check=False)
    detached = _read_json(workspace / "detached.json")
    return {
        "returncode": completed.returncode,
        "detached": detached,
        "outside_written": (sibling / "detached-outside.txt").exists(),
        "is_codex_evidence": False,
    }


def run_live() -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v05-inheritance-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        sibling = root / "sibling"
        workspace.mkdir()
        sibling.mkdir()
        fixture = workspace / "inheritance_fixture.py"
        fixture.write_text(FIXTURE, encoding="utf-8")
        approvals = DecliningApprovals()
        process = AppServerProcess(cwd=workspace, request_handler=approvals)
        controller = AgentController(ThreadRegistry(root / "threads.json"), process=process)
        turn: Dict[str, Any] = {}
        turn_id: Optional[str] = None
        error: Optional[str] = None
        try:
            controller.start(timeout=30)
            controller.create_thread(cwd=workspace, sandbox="workspace-write", approval_policy="on-request", timeout=30)
            turn = controller.start_turn(
                "Execute exactly this command once using the terminal tool: python3 inheritance_fixture.py parent. Do not simulate or replace it.",
                timeout=30,
            )
            turn_id = str(turn["id"])
            turn = controller.wait_turn(turn_id, timeout=90)
        except (AgentControllerError, OSError, RuntimeError, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            raw_snapshot = process.snapshot()
            snapshot = compact_snapshot(raw_snapshot)
            process.stop()
        roles = {name: _read_json(workspace / f"{name}.json") for name in ("parent", "child", "grandchild", "detached")}
        fixture_exercised = roles["parent"] is not None or roles["child"] is not None or roles["detached"] is not None
        outside_written = (sibling / "detached-outside.txt").exists()
        command_events = []
        for notification in raw_snapshot.get("notifications", []):
            if notification.get("method") not in {"item/started", "item/completed"}:
                continue
            item = (notification.get("params") or {}).get("item")
            if isinstance(item, dict) and item.get("command"):
                command_events.append({"method": notification.get("method"), "command": item.get("command"), "process_id": item.get("processId"), "exit_code": item.get("exitCode")})
        if not fixture_exercised:
            sandbox_status = "UNKNOWN"
            approval_status = "UNKNOWN"
        elif outside_written:
            sandbox_status = "FAIL"
            approval_status = "FAIL" if not approvals.requests else "PARTIAL"
        else:
            sandbox_status = "PASS"
            approval_status = "PASS" if approvals.requests and any(item.get("method") == "item/commandExecution/requestApproval" for item in approvals.requests) else "UNKNOWN"
        value = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "backend": "SANDBOX_DESCENDANT_SUPERVISOR",
            "status": "PASS" if sandbox_status == approval_status == "PASS" else "PARTIAL" if fixture_exercised else "UNKNOWN",
            "thread_id": controller.thread_id,
            "turn_id": turn_id,
            "turn": turn,
            "command_events": command_events,
            "fixture_exercised": fixture_exercised,
            "roles": roles,
            "outside_written": outside_written,
            "approval_requests": approvals.requests,
            "sandbox_inheritance": sandbox_status,
            "approval_integrity": approval_status,
            "parent_exited_before_descendant": bool((roles.get("parent") or {}).get("parent_exited_before_descendant")),
            "detached_descendant_observed": roles.get("detached") is not None,
            "host_reference": _run_host_reference(root),
            "error": error,
            "app_server_snapshot": snapshot,
            "controller_spawn_used": False,
            "launchd_or_external_daemon_used": False,
            "notes": [
                "The host reference is explicitly excluded from Codex capability status.",
                "The normal thread tool is the only source accepted for descendant-supervisor evidence.",
            ],
        }
        return _scrub(redact(value), root)


def markdown(value: Dict[str, Any]) -> str:
    return (
        "# v0.5 descendant supervisor and process inheritance\n\n"
        f"Status: **{value.get('status')}**\n\n"
        f"Sandbox inheritance: **{value.get('sandbox_inheritance')}**\n\n"
        f"Approval integrity: **{value.get('approval_integrity')}**\n\n"
        f"Fixture exercised by normal tool: `{value.get('fixture_exercised')}`\n\n"
        f"Outside write: `{value.get('outside_written')}`\n\n"
        f"Parent exited before descendant: `{value.get('parent_exited_before_descendant')}`\n\n"
        "The host reference is retained only to demonstrate why a direct local subprocess test cannot prove Codex sandbox inheritance.\n"
    )


def main() -> int:
    value = run_live()
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "process-inheritance.json", value)
    (OUT / "process-inheritance.md").write_text(markdown(value), encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
