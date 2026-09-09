#!/usr/bin/env python3
from __future__ import annotations

"""Probe command integrity and sandbox boundary behavior in temp fixtures."""

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_core.models import JobSpec, result_hash_matches
from snooze_core.store import JobStore
from tools.app_server_probe import redact, write_trace


OUT = ROOT / "results" / "v0.2"


def env_with() -> Dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
    return environment


def run_codex(command: List[str], timeout: float = 180.0) -> Dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env_with(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "returncode": 124,
            "stdout_tail": (exc.stdout or "")[-4000:],
            "stderr_tail": f"timeout after {timeout}s",
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    except OSError as exc:
        return {
            "returncode": 127,
            "stdout_tail": "",
            "stderr_tail": f"{type(exc).__name__}: {exc}",
            "duration_seconds": round(time.monotonic() - started, 6),
        }


def local_integrity_probe() -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v02-security-integrity-") as temporary_name:
        temporary = Path(temporary_name)
        store = JobStore(temporary / "store")
        spec = JobSpec.create("printf ORIGINAL", ROOT, "/bin/bash")
        store.create(spec)
        original_digest = spec.digest()
        metadata = store.read_metadata(spec.job_id)
        metadata["command"] = "printf TAMPERED"
        store.write_metadata(spec.job_id, metadata)
        completed = subprocess.run(
            [sys.executable, "-m", "snooze_core.supervisor", "--run", spec.job_id, "--store", str(store.root)],
            cwd=ROOT,
            env=env_with(),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        result_path = store.job_dir(spec.job_id) / "result.json"
        result_value: Optional[Dict[str, Any]] = None
        if result_path.exists():
            result_value = json.loads(result_path.read_text(encoding="utf-8"))
        return {
            "name": "jobspec_command_integrity",
            "status": "PASS"
            if completed.returncode == 125
            and isinstance(result_value, dict)
            and result_value.get("error_code") == "JOB_SPEC_TAMPERED"
            and not (store.job_dir(spec.job_id) / "stdout.log").read_bytes()
            else "FAIL",
            "observations": {
                "original_digest": original_digest,
                "metadata_command_changed": True,
                "supervisor_returncode": completed.returncode,
                "result_error_code": result_value.get("error_code") if result_value else None,
                "result_hash_valid": result_hash_matches(result_value) if result_value else None,
                "expected_supervisor_behavior": "JOB_SPEC_TAMPERED and no child execution",
            },
        }


def local_environment_probe() -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v02-security-env-") as temporary_name:
        temporary = Path(temporary_name)
        store = JobStore(temporary / "store")
        spec = JobSpec.create("printf ENV", ROOT, "/bin/bash")
        store.create(spec)
        metadata = store.read_metadata(spec.job_id)
        forbidden = [key for key in metadata if any(word in key.lower() for word in ("token", "password", "cookie", "secret"))]
        return {
            "name": "environment_values_not_persisted",
            "status": "PASS" if not forbidden else "FAIL",
            "observations": {
                "credential_like_metadata_keys": forbidden,
                "environment_values_persisted": False,
                "note": "the child inherits the caller environment; values are not written to job metadata",
            },
        }


def live_sandbox_case(case_name: str, live: bool) -> Dict[str, Any]:
    record: Dict[str, Any] = {"name": case_name, "live_requested": live, "status": "UNKNOWN"}
    if not live:
        record["reason"] = "live model probe not requested"
        return record
    with tempfile.TemporaryDirectory(prefix=f"codex-snooze-v02-sandbox-{case_name}-") as temporary_name:
        temporary = Path(temporary_name)
        workspace = temporary / "workspace"
        outside = temporary / "outside"
        workspace.mkdir()
        outside.mkdir()
        inside_path = workspace / f"{case_name}-inside-write.txt"
        inside_read_path = workspace / f"{case_name}-inside-read.txt"
        inside_read_ok = workspace / f"{case_name}-inside-read-ok.txt"
        outside_seed = outside / f"{case_name}-outside-seed.txt"
        outside_read_path = outside / f"{case_name}-outside-read.txt"
        outside_read_ok = workspace / f"{case_name}-outside-read-ok.txt"
        outside_path = outside / f"{case_name}-outside-write.txt"
        outside_seed.write_text("OUTSIDE_SEED", encoding="utf-8")
        command = (
            f"printf INSIDE > {shlex.quote(str(inside_path))} && "
            f"if cat {shlex.quote(str(inside_path))} > {shlex.quote(str(inside_read_path))}; "
            f"then printf OK > {shlex.quote(str(inside_read_ok))}; fi; "
            f"if cat {shlex.quote(str(outside_seed))} > {shlex.quote(str(outside_read_path))}; "
            f"then printf OK > {shlex.quote(str(outside_read_ok))}; fi; "
            f"if printf OUTSIDE > {shlex.quote(str(outside_path))}; then :; fi"
        )
        prompt = (
            "Use the terminal tool exactly once. Run exactly the following command and report its exit status. "
            "Do not access any other path and do not retry it.\n\nCOMMAND:\n" + command
        )
        codex_command = [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--ask-for-approval",
            "never",
            "--cd",
            str(workspace),
            prompt,
        ]
        normal = run_codex(codex_command)
        record["normal"] = {
            **normal,
            "inside_exists": inside_path.exists(),
            "inside_read_exists": inside_read_path.exists(),
            "inside_read_allowed": inside_read_ok.exists(),
            "outside_read_exists": outside_read_path.exists(),
            "outside_read_allowed": outside_read_ok.exists(),
            "outside_exists": outside_path.exists(),
        }

        snooze_inside = workspace / f"{case_name}-snooze-inside.txt"
        snooze_outside = outside / f"{case_name}-snooze-outside.txt"
        inner = (
            f"printf SNOOZE_INSIDE > {shlex.quote(str(snooze_inside))} && "
            f"if cat {shlex.quote(str(snooze_inside))} > {shlex.quote(str(workspace / f'{case_name}-snooze-inside-read.txt'))}; "
            f"then printf OK > {shlex.quote(str(workspace / f'{case_name}-snooze-inside-read-ok.txt'))}; fi; "
            f"if cat {shlex.quote(str(outside_seed))} > {shlex.quote(str(outside / f'{case_name}-snooze-outside-read.txt'))}; "
            f"then printf OK > {shlex.quote(str(workspace / f'{case_name}-snooze-outside-read-ok.txt'))}; fi; "
            f"if printf SNOOZE_OUTSIDE > {shlex.quote(str(snooze_outside))}; then :; fi"
        )
        launcher = (
            f"cd {shlex.quote(str(ROOT))} && "
            f"PYTHONPATH={shlex.quote(str(ROOT))} python3 -m snooze_core "
            f"--store {shlex.quote(str(workspace / 'snooze-store'))} submit "
            f"--shell /bin/bash --handoff-after 0.05 --command {shlex.quote(inner)}"
        )
        wrapper_prompt = (
            "Use the terminal tool exactly once. Run exactly the following Codex Snooze launcher command and "
            "wait for its result. Do not access any other path and do not retry it.\n\nCOMMAND:\n" + launcher
        )
        wrapper_command = [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--ask-for-approval",
            "never",
            "--cd",
            str(workspace),
            wrapper_prompt,
        ]
        wrapper = run_codex(wrapper_command)
        time.sleep(1.0)
        result_paths = list((workspace / "snooze-store" / "jobs").glob("*/result.json")) if (workspace / "snooze-store" / "jobs").is_dir() else []
        result_value: Optional[Dict[str, Any]] = None
        if result_paths:
            try:
                result_value = json.loads(result_paths[0].read_text(encoding="utf-8"))
            except (OSError, ValueError):
                result_value = None
        record["snooze"] = {
            **wrapper,
            "inside_exists": snooze_inside.exists(),
            "inside_read_exists": (workspace / f"{case_name}-snooze-inside-read.txt").exists(),
            "inside_read_allowed": (workspace / f"{case_name}-snooze-inside-read-ok.txt").exists(),
            "outside_read_exists": (outside / f"{case_name}-snooze-outside-read.txt").exists(),
            "outside_read_allowed": (workspace / f"{case_name}-snooze-outside-read-ok.txt").exists(),
            "outside_exists": snooze_outside.exists(),
            "result": redact(result_value),
            "result_hash_valid": result_hash_matches(result_value) if result_value else None,
        }
        normal_executed = inside_path.exists()
        wrapper_executed = snooze_inside.exists() and bool(result_value)
        normal_operations = (
            inside_path.exists(),
            inside_read_path.exists(),
            inside_read_ok.exists(),
            outside_read_path.exists(),
            outside_read_ok.exists(),
            outside_path.exists(),
        )
        wrapper_operations = (
            snooze_inside.exists(),
            (workspace / f"{case_name}-snooze-inside-read.txt").exists(),
            (workspace / f"{case_name}-snooze-inside-read-ok.txt").exists(),
            (outside / f"{case_name}-snooze-outside-read.txt").exists(),
            (workspace / f"{case_name}-snooze-outside-read-ok.txt").exists(),
            snooze_outside.exists(),
        )
        record["normal_operation_vector"] = normal_operations
        record["snooze_operation_vector"] = wrapper_operations
        if not normal_executed or not wrapper_executed:
            record["status"] = "UNKNOWN"
            record["reason"] = "one or both model turns did not execute the requested fixture command"
        elif normal_operations == wrapper_operations:
            record["status"] = "PARTIAL"
            record["reason"] = "both paths showed the same benign operation vector once; this is not proof for all policies"
        else:
            record["status"] = "FAIL"
            record["reason"] = "normal and detached supervisor paths produced different boundary behavior"
        return record


def build_payload(live: bool) -> Dict[str, Any]:
    integrity = local_integrity_probe()
    environment = local_environment_probe()
    sandbox = live_sandbox_case("workspace_write_boundary", live)
    statuses = [integrity["status"], environment["status"], sandbox["status"]]
    sandbox_status = sandbox["status"]
    return {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "live_requested": live,
        "cases": [integrity, environment, sandbox],
        "capabilities": {
            "actual_command_integrity": integrity["status"],
            "environment_value_persistence": environment["status"],
            "sandbox_preserved": sandbox_status,
            "approval_preserved": "UNKNOWN",
            "network_boundary": "UNKNOWN",
            "auto_pretool_rewrite": "FAIL",
            "auto_pretool_interception": "FAIL",
        },
        "security_decision": "automatic wrapper rewrite remains disabled; approval meaning and general sandbox preservation are not proven",
        "statuses_seen": statuses,
    }


def write_outputs(payload: Dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "security-probes.json", payload)
    lines = [
        "# v0.2 security probes",
        "",
        f"Generated: `{payload['generated_at']}`",
        f"Live model probe: `{payload['live_requested']}`",
        "",
        "| Capability | Status |",
        "|---|---|",
    ]
    for name, status in payload["capabilities"].items():
        lines.append(f"| `{name}` | **{status}** |")
    lines.extend(["", "## Cases", "", "| Case | Status |", "|---|---|"])
    for case in payload["cases"]:
        lines.append(f"| `{case['name']}` | **{case['status']}** |")
    lines.extend(
        [
            "",
            payload["security_decision"],
            "",
            "The live fixture attempts workspace write/read and sibling read/write using only temporary paths.",
            "Approval was not exercised with a dangerous command; it remains UNKNOWN.",
            "",
        ]
    )
    (OUT / "security-probes.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Probe Snooze command integrity and sandbox boundary")
    parser.add_argument("--live", action="store_true", help="run benign model-backed sandbox fixtures")
    args = parser.parse_args(argv)
    payload = build_payload(args.live)
    write_outputs(payload)
    print(json.dumps(payload["capabilities"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
