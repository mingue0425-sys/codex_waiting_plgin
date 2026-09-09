#!/usr/bin/env python3
from __future__ import annotations

"""Compare the explicit command/exec handoff sandbox with normal Codex."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.4"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.agent import AgentController, AgentControllerError
from snooze_controller.app_server_process import AppServerProcess
from snooze_controller.model_policy import (
    LUNA_MODEL,
    REASONING_EFFORT,
    attach_model_metadata,
    attest_model,
    emit_model_policy_log,
    ensure_luna_exec_command,
    run_luna_exec_attestation,
    write_model_attestation,
)
from snooze_controller.thread_registry import ThreadRegistry
from tools.app_server_probe import redact, utc_now, write_trace

FIXTURE = ROOT / "probes" / "fixtures" / "security_fixture.py"


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
            value = value.replace(candidate, "<v04-security-root>")
        return value.replace(str(ROOT), "<project-root>")
    return value


def preflight(workspace: Path, env: Dict[str, str]) -> tuple[Any, Dict[str, Any]]:
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
    completed, events, attestation = run_luna_exec_attestation(command, cwd=ROOT, env=env, timeout=150)
    emit_model_policy_log(attestation)
    return attestation, {"returncode": completed.returncode, "event_types": sorted({str(item.get("type")) for item in events}), "stdout_tail": completed.stdout[-2000:], "stderr_tail": completed.stderr[-2000:]}


def normal_codex(workspace: Path, outside: Path, env: Dict[str, str]) -> Dict[str, Any]:
    prompt = (
        "Execute exactly this benign command once using the terminal tool and report its JSON output; do not "
        f"simulate it: python3 security_fixture.py --inside inside.txt --outside {outside}."
    )
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
        prompt,
    ])
    try:
        completed = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=150, check=False)
        events = []
        for line in completed.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        return {
            "returncode": completed.returncode,
            "inside_written": (workspace / "inside.txt").exists(),
            "outside_written": outside.exists(),
            "event_types": sorted({str(item.get("type")) for item in events if isinstance(item, dict)}),
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
            "fixture_exercised": (workspace / "inside.txt").exists(),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "inside_written": False, "outside_written": outside.exists(), "fixture_exercised": False}


def owned_server(workspace: Path, outside: Path, env: Dict[str, str]) -> Dict[str, Any]:
    registry = ThreadRegistry(workspace.parent / "threads.json")
    process = AppServerProcess(cwd=ROOT, env=env, experimental_api=True)
    controller = AgentController(registry, process=process)
    result: Dict[str, Any] = {}
    try:
        process.start(timeout=30)
        thread = controller.create_thread(cwd=workspace, sandbox="workspace-write", approval_policy="on-request", timeout=30)
        policy = {"type": "workspaceWrite", "writableRoots": [str(workspace.resolve())], "networkAccess": False}
        command = [
            sys.executable,
            str(FIXTURE),
            "--inside",
            "inside.txt",
            "--outside",
            str(outside),
        ]
        response = process.request(
            "command/exec",
            {
                "command": command,
                "cwd": str(workspace.resolve()),
                "disableTimeout": True,
                "sandboxPolicy": policy,
            },
            timeout=60,
        )
        result.update(
            {
                "thread_start": thread,
                "command_response": response,
                "inside_written": (workspace / "inside.txt").exists(),
                "outside_written": outside.exists(),
                "environment_marker": _environment_marker(process, workspace),
                "server_requests": [
                    {"id": item.request_id, "method": item.method, "params": item.params}
                    for item in process.server_requests
                ],
                "approval_rejected_explicitly": any(item.method in {"execCommandApproval", "item/commandExecution/requestApproval"} for item in process.server_requests)
                and any((event.get("kind") == "server_response" and (event.get("error") or {}).get("code") == -32001) for event in process.events),
                "sandbox_policy_sent": policy,
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
    finally:
        process.stop()
    return result


def _environment_marker(process: AppServerProcess, workspace: Path) -> Optional[str]:
    response = process.request(
        "command/exec",
        {
            "command": [sys.executable, "-c", "import os; print(os.environ.get('CODEX_SNOOZE_BENIGN_MARKER', ''))"],
            "cwd": str(workspace.resolve()),
            "disableTimeout": True,
            "sandboxPolicy": {"type": "workspaceWrite", "writableRoots": [str(workspace.resolve())]},
        },
        timeout=30,
    )
    return str((response.get("result") or {}).get("stdout", "")).strip() if isinstance(response, dict) else None


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v04-security-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        sibling = root / "sibling"
        workspace.mkdir()
        sibling.mkdir()
        (workspace / "security_fixture.py").write_bytes(FIXTURE.read_bytes())
        outside = sibling / "outside.txt"
        env = os.environ.copy()
        env["CODEX_SNOOZE_BENIGN_MARKER"] = "V04_BENIGN_ENV"
        owned_workspace = root / "owned-workspace"
        owned_sibling = root / "owned-sibling"
        owned_workspace.mkdir()
        owned_sibling.mkdir()
        (owned_workspace / "security_fixture.py").write_bytes(FIXTURE.read_bytes())
        owned_outside = owned_sibling / "outside.txt"
        try:
            attestation, preflight_result = preflight(workspace, env)
        except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
            attestation = attest_model(requested_model=LUNA_MODEL, runtime_reported_model=None, thread_model=None, turn_model=None, reasoning_effort=REASONING_EFFORT)
            preflight_result = {"error": f"{type(exc).__name__}: {exc}"}
            emit_model_policy_log(attestation)
        if attestation.verified:
            normal = normal_codex(workspace, outside, env)
            owned = owned_server(owned_workspace, owned_outside, env)
        else:
            normal = {"status": "NOT_RUN", "reason": "MODEL_ATTESTATION=FAIL; normal candidate was not started"}
            owned = {"status": "NOT_RUN", "reason": "MODEL_ATTESTATION=FAIL; owned candidate was not started"}
        owned_boundary = owned.get("inside_written") is True and owned.get("outside_written") is False
        normal_exercised = normal.get("fixture_exercised") is True
        normal_boundary = normal.get("inside_written") is True and normal.get("outside_written") is False
        owned_broader = owned.get("outside_written") is True
        sandbox_parity = "FAIL" if owned_broader else "PASS" if normal_exercised and owned_boundary and normal_boundary else "PARTIAL" if owned_boundary else "UNKNOWN"
        owned_approval = owned.get("approval_rejected_explicitly") is True or not owned.get("outside_written", False)
        approval_parity = (
            "FAIL"
            if owned_broader and not owned.get("approval_rejected_explicitly")
            else "PASS"
            if normal_exercised and owned_approval and not owned.get("outside_written")
            else "PARTIAL"
            if owned_approval
            else "UNKNOWN"
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
            "preflight": preflight_result,
            "non_luna_model_calls": attestation.non_luna_model_calls,
            "status": "PASS" if attestation.verified and sandbox_parity == "PASS" and approval_parity == "PASS" else "PARTIAL" if attestation.verified and sandbox_parity in {"PASS", "PARTIAL"} else "UNKNOWN",
            "sandbox_parity": sandbox_parity,
            "approval_parity": approval_parity,
            "normal_codex": normal,
            "snooze_owned_app_server": owned,
            "environment_marker": "V04_BENIGN_ENV" if owned.get("environment_marker") == "V04_BENIGN_ENV" else "UNKNOWN",
            "automatic_approval": "DISABLED",
            "production_automation": "DISABLED_SECURITY_GATE",
            "owned_path_wider_than_workspace": owned_broader,
            "normal_fixture_exercised": normal_exercised,
            "scope": "temporary workspace, sibling, and benign environment marker only",
        }
        value = attach_model_metadata(value, attestation, experiment_valid=False)
        value = scrub(redact(value), root)
    OUT.mkdir(parents=True, exist_ok=True)
    if value["sandbox_parity"] == "FAIL":
        value["status"] = "FAIL"
    write_trace(OUT / "security-parity.json", value)
    write_model_attestation(OUT / "model-attestation.json", attestation, experiment="v0.4_security_parity", experiment_valid=False)
    (OUT / "security-parity.md").write_text(
        "# v0.4 sandbox and approval parity\n\n"
        f"Status: **{value['status']}**\n\n"
        f"Sandbox parity: **{value['sandbox_parity']}**\n\n"
        f"Approval parity: **{value['approval_parity']}**\n\n"
        "The handoff tool uses command/exec with an explicit workspaceWrite policy. "
        "Approval requests are rejected explicitly unless a caller supplies a handler; no blanket allow exists.\n",
        encoding="utf-8",
    )
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
