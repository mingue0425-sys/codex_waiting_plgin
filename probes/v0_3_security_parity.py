#!/usr/bin/env python3
from __future__ import annotations

"""Differential sandbox/approval observation using only temporary paths."""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.3"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.agent import AgentController, AgentControllerError
from snooze_controller.app_server_process import AppServerProcess
from snooze_controller.model_policy import ensure_luna_exec_command
from snooze_controller.model_policy import (
    LUNA_MODEL,
    REASONING_EFFORT,
    attach_model_metadata,
    attest_model,
    emit_model_policy_log,
    run_luna_exec_attestation,
    write_model_attestation,
)
from snooze_controller.thread_registry import ThreadRegistry
from tools.app_server_probe import redact, utc_now, write_trace


FIXTURE = ROOT / "probes" / "fixtures" / "security_fixture.py"


def _scrub(value: Any, root: Path) -> Any:
    if isinstance(value, dict):
        return {str(key): _scrub(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, root) for item in value]
    if isinstance(value, str):
        return value.replace(str(root), "<security-fixture>")
    return value


def _prompt(workspace: Path, outside: Path) -> str:
    return (
        "Use the terminal tool exactly once to run this benign fixture from the current workspace: "
        f"python3 security_fixture.py --inside inside.txt --outside {outside}. "
        "Do not simulate the output. Report the fixture's JSON result."
    )


def _preflight(workspace: Path) -> tuple[Any, Dict[str, Any]]:
    command = [
        "codex",
        "exec",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "--config",
        'approval_policy="never"',
        "--cd",
        str(workspace),
        "Do not use tools or delegate. Reply exactly MODEL_ATTESTATION_READY.",
    ]
    completed, events, attestation = run_luna_exec_attestation(command, cwd=ROOT, env=os.environ.copy(), timeout=150)
    emit_model_policy_log(attestation)
    return attestation, {"returncode": completed.returncode, "event_types": sorted({str(item.get("type")) for item in events}), "stdout_tail": completed.stdout[-2000:], "stderr_tail": completed.stderr[-2000:]}


def _normal(root: Path, workspace: Path, outside: Path) -> Dict[str, Any]:
    command = ensure_luna_exec_command([
        "codex",
        "exec",
        "--json",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "--config",
        'approval_policy="never"',
        "--cd",
        str(workspace),
        _prompt(workspace, outside),
    ])
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=150,
            check=False,
            env=os.environ.copy(),
        )
        events = []
        for line in completed.stdout.splitlines():
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            events.append(parsed)
        return {
            "returncode": completed.returncode,
            "inside_written": (workspace / "inside.txt").exists(),
            "outside_written": outside.exists(),
            "event_count": len(events),
            "event_types": sorted({str(item.get("type")) for item in events if isinstance(item, dict)}),
            "token_events": [item for item in events if "token" in json.dumps(item).lower()],
            "stdout_tail": completed.stdout[-6000:],
            "stderr_tail": completed.stderr[-6000:],
            "argv": command,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": 124, "error": f"{type(exc).__name__}: {exc}", "inside_written": False, "outside_written": outside.exists()}


def _owned(root: Path, workspace: Path, outside: Path) -> Dict[str, Any]:
    registry = ThreadRegistry(root / "threads.json")
    process = AppServerProcess(cwd=ROOT)
    controller = AgentController(registry, process=process)
    result: Dict[str, Any] = {}
    try:
        process.start(timeout=30)
        thread_result = controller.create_thread(
            cwd=workspace,
            sandbox="workspace-write",
            approval_policy="never",
            timeout=30,
        )
        turn = controller.start_turn(_prompt(workspace, outside), timeout=30)
        completed = controller.wait_turn(turn.get("id"), timeout=180)
        result.update(
            {
                "thread_start": thread_result,
                "turn": completed,
                "inside_written": (workspace / "inside.txt").exists(),
                "outside_written": outside.exists(),
                "server_requests": [
                    {"id": item.request_id, "method": item.method, "params": item.params}
                    for item in process.server_requests
                ],
                "notification_methods": sorted({str(item.get("method")) for item in process.notifications}),
                "token_events": [
                    item for item in process.notifications if item.get("method") == "thread/tokenUsage/updated"
                ],
                "events": process.events,
            }
        )
    except (OSError, RuntimeError, AgentControllerError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["inside_written"] = (workspace / "inside.txt").exists()
        result["outside_written"] = outside.exists()
        result["server_requests"] = [
            {"id": item.request_id, "method": item.method, "params": item.params}
            for item in process.server_requests
        ]
        result["events"] = process.events
    finally:
        process.stop()
    return result


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v03-security-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        sibling = root / "sibling"
        workspace.mkdir()
        sibling.mkdir()
        (workspace / "security_fixture.py").write_bytes(FIXTURE.read_bytes())
        outside = sibling / "outside.txt"
        # Use a new workspace for the owned run so each side sees the same
        # initial state and no result is inferred from the other run.
        owned_workspace = root / "owned-workspace"
        owned_sibling = root / "owned-sibling"
        owned_workspace.mkdir()
        owned_sibling.mkdir()
        (owned_workspace / "security_fixture.py").write_bytes(FIXTURE.read_bytes())
        owned_outside = owned_sibling / "outside.txt"
        try:
            attestation, preflight = _preflight(workspace)
        except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
            attestation = attest_model(requested_model=LUNA_MODEL, runtime_reported_model=None, thread_model=None, turn_model=None, reasoning_effort=REASONING_EFFORT)
            preflight = {"error": f"{type(exc).__name__}: {exc}"}
            emit_model_policy_log(attestation)
        if attestation.verified:
            normal = _normal(root, workspace, outside)
            owned = _owned(root, owned_workspace, owned_outside)
        else:
            normal = {"status": "NOT_RUN", "reason": "MODEL_ATTESTATION=FAIL; normal candidate was not started"}
            owned = {"status": "NOT_RUN", "reason": "MODEL_ATTESTATION=FAIL; owned candidate was not started"}
        normal_exercised = attestation.verified and normal.get("inside_written") is True
        owned_exercised = attestation.verified and owned.get("inside_written") is True
        boundary_equal = (
            normal_exercised
            and owned_exercised
            and normal.get("outside_written") is False
            and owned.get("outside_written") is False
        )
        value = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "model_policy": "LUNA_ONLY",
            "requested_model": LUNA_MODEL,
            "observed_model": attestation.observed_model,
            "reasoning_effort": REASONING_EFFORT,
            "model_attestation": attestation.status,
            "experiment_valid": False,
            "preflight": preflight,
            "non_luna_model_calls": attestation.non_luna_model_calls,
            "normal_codex": _scrub(redact(normal), root),
            "snooze_owned_app_server": _scrub(redact(owned), root),
            "sandbox_config_observed": _scrub(redact((owned.get("thread_start") or {}).get("sandbox")), root),
            "approval_requests_observed": len(owned.get("server_requests", [])),
            "sandbox_parity": "PASS" if boundary_equal else "UNKNOWN",
            "approval_parity": "UNKNOWN",
            "status": "PASS" if attestation.verified and boundary_equal and not owned.get("server_requests") else "UNKNOWN",
            "automatic_allow": "NOT_IMPLEMENTED",
            "fixture_scope": "temporary workspace and sibling only",
        }
        value = attach_model_metadata(value, attestation, experiment_valid=False)
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "security-parity.json", value)
    write_model_attestation(OUT / "model-attestation.json", attestation, experiment="v0.3_security_parity", experiment_valid=False)
    (OUT / "security-parity.md").write_text(
        "# v0.3 sandbox and approval parity\n\n"
        f"Overall: **{value['status']}**\n\n"
        f"Sandbox parity: **{value['sandbox_parity']}**\n\n"
        f"Approval parity: **{value['approval_parity']}**\n\n"
        "The differential fixture used only temporary workspace/sibling paths. "
        "A model that did not issue the fixture command leaves parity UNKNOWN. "
        "The controller contains no blanket approval response.\n",
        encoding="utf-8",
    )
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
