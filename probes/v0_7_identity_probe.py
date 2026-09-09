#!/usr/bin/env python3
from __future__ import annotations

"""Run the short self-authenticating fixture through a normal thread."""

import argparse
import hashlib
import json
import secrets
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.7"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from probes.v0_6_support import ScopedApprovalRecorder
from probes.v0_7_native_thread import _first_probe_record, _json, _output_strings, _item, command_notifications, inspect_pid
from snooze_controller.agent import AgentController, AgentControllerError
from snooze_controller.app_server_process import AppServerProcess
from snooze_controller.thread_registry import ThreadRegistry
from snooze_controller.v07_provenance import correlate_probe_evidence, redact_identity
from snooze_core.models import utc_now
from tools.app_server_probe import redact, write_trace


def run_once() -> Dict[str, Any]:
    run_root = ROOT / "runtime_probe_runs" / f"identity-{secrets.token_hex(8)}"
    run_root.mkdir(parents=True, exist_ok=False)
    nonce = f"SNOOZE_PROBE_{secrets.token_hex(16)}"
    marker = run_root / "markers" / f"{nonce}.json"
    result_path = run_root / "result.json"
    script = (ROOT / "fixtures" / "runtime_identity_probe.py").resolve()
    command = f"python3 {script} --nonce {nonce} --marker {marker} --result {result_path}"
    process = AppServerProcess(cwd=run_root, experimental_api=True, request_handler=ScopedApprovalRecorder("decline", expected_fragment=script.name))
    controller = AgentController(process=process, registry=ThreadRegistry(run_root / "thread-registry.json"), cwd=run_root)
    error = None
    turn: Dict[str, Any] = {}
    try:
        controller.start(timeout=40)
        controller.create_thread(cwd=run_root, sandbox="workspace-write", approval_policy="on-request", timeout=40)
        turn = controller.start_turn(
            "Use the normal terminal execution tool and execute exactly this command once. "
            f"Do not replace or wrap it and do not run any other command: {command}. "
            "Do not use command/exec, process/spawn, thread/shellCommand, wait, or write_stdin.",
            timeout=40,
        )
        controller.wait_turn(str(turn["id"]), timeout=60)
    except (AgentControllerError, OSError, RuntimeError, ValueError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        snapshot = process.snapshot()
        process.stop()
    notifications = snapshot.get("notifications", [])
    records = command_notifications(notifications)
    output_text = "\n".join(text for notification in notifications for text in _output_strings(notification.get("params")))
    report = _first_probe_record(output_text, nonce)
    marker_record = _json(marker)
    result_record = _json(result_path)
    item_record = next((item["item"] for item in records if item["method"] == "item/started"), {})
    observation = inspect_pid(report.get("pid") if report else None, command)
    evidence = correlate_probe_evidence(
        expected_nonce=nonce,
        item_record=item_record,
        output_text=output_text,
        marker_record=marker_record,
        self_report=report,
        os_observation=observation,
        expected_cwd=str(run_root.resolve()),
        expected_command=None,
        logical_process_id=item_record.get("process_id"),
    )
    value = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "fixture": {"kind": "short_identity", "nonce": nonce, "command": command, "command_sha256": hashlib.sha256(command.encode("utf-8")).hexdigest()},
        "execution_namespace": {"controller_workspace": str(run_root.resolve()), "app_server_cwd": snapshot.get("cwd"), "same_absolute_namespace": snapshot.get("cwd") == str(run_root.resolve()), "marker_observed": marker_record is not None, "result_observed": result_record is not None},
        "command_records": records,
        "stdout_evidence": output_text[-12000:],
        "self_report": report,
        "marker_record": marker_record,
        "result_record": result_record,
        "os_observation": observation,
        "provenance": evidence,
        "turn": turn,
        "error": error,
        "controller_candidate_execution_used": False,
        "status": evidence.get("status", "UNKNOWN"),
    }
    value = redact_identity(redact(value), project_root=ROOT)
    shutil.rmtree(run_root, ignore_errors=True)
    return value


def main() -> int:
    value = run_once()
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "identity-live-observation.json", value)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
