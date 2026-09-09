#!/usr/bin/env python3
from __future__ import annotations

"""Verify that source-tree changes are reported as COMPLETED_STALE.

Every case uses a disposable Git repository and changes exactly one tracked
state dimension while a deterministic sleep job is running.  The probe never
touches the project repository or a user's branch.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.2"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_core.git_fingerprint import fingerprint
from snooze_core.models import ExecutionState, JobSpec
from snooze_core.store import JobStore


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def _init_repo(repo: Path) -> None:
    result = subprocess.run(
        ["git", "init", "-q", str(repo)], capture_output=True, text=True, check=False, timeout=10
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git init failed")
    (repo / "tracked.txt").write_text("initial\n", encoding="utf-8")
    commit = _git(
        repo,
        "-c",
        "user.name=Codex Snooze v0.2",
        "-c",
        "user.email=codex-snooze-v02@example.invalid",
        "add",
        "tracked.txt",
    )
    if commit.returncode != 0:
        raise RuntimeError(commit.stderr.strip() or "git add failed")
    commit = _git(
        repo,
        "-c",
        "user.name=Codex Snooze v0.2",
        "-c",
        "user.email=codex-snooze-v02@example.invalid",
        "commit",
        "-q",
        "-m",
        "initial",
    )
    if commit.returncode != 0:
        raise RuntimeError(commit.stderr.strip() or "git commit failed")


def _wait_running(store: JobStore, job_id: str, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if store.read_metadata(job_id).get("execution_state") == ExecutionState.RUNNING.value:
            return
        time.sleep(0.02)
    raise TimeoutError("supervisor did not reach RUNNING")


def _run_case(name: str, mutate: Callable[[Path], None], temporary: Path) -> Dict[str, Any]:
    repo = temporary / name
    repo.mkdir()
    _init_repo(repo)
    store = JobStore(temporary / f"{name}-store")
    spec = JobSpec.create("sleep 0.45; printf STALE_PROBE_DONE", repo, "/bin/bash")
    store.create(spec)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    supervisor = subprocess.Popen(
        [sys.executable, "-m", "snooze_core.supervisor", "--run", spec.job_id, "--store", str(store.root)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    record: Dict[str, Any] = {"name": name, "status": "UNKNOWN"}
    try:
        _wait_running(store, spec.job_id)
        before = fingerprint(repo)
        mutate(repo)
        after_mutation = fingerprint(repo)
        stdout, stderr = supervisor.communicate(timeout=10)
        result = store.read_result(spec.job_id)
        record.update(
            {
                "before_mutation": before,
                "after_mutation": after_mutation,
                "supervisor_returncode": supervisor.returncode,
                "supervisor_stdout": stdout,
                "supervisor_stderr": stderr,
                "result": result,
            }
        )
        if not isinstance(result, dict):
            record["reason"] = "result.json was not created"
        elif (
            result.get("execution_state") == ExecutionState.COMPLETED_STALE.value
            and result.get("exit_code") == 0
            and result.get("source_state_changed") is True
            and result.get("project_fingerprint_start") != result.get("project_fingerprint_end")
            and before != after_mutation
        ):
            record["status"] = "PASS"
        else:
            record["status"] = "FAIL"
            record["reason"] = "mutation was not reflected as an authoritative stale result"
    except (OSError, RuntimeError, TimeoutError, subprocess.SubprocessError) as exc:
        record["status"] = "FAIL"
        record["reason"] = f"{type(exc).__name__}: {exc}"
        if supervisor.poll() is None:
            supervisor.kill()
        try:
            supervisor.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            supervisor.kill()
            supervisor.communicate()
    return record


def _tracked_change(repo: Path) -> None:
    (repo / "tracked.txt").write_text("modified while running\n", encoding="utf-8")


def _untracked_add(repo: Path) -> None:
    (repo / "new-untracked.txt").write_text("created while running\n", encoding="utf-8")


def _branch_change(repo: Path) -> None:
    result = _git(repo, "switch", "-c", "snooze-probe-branch")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git switch failed")


def _head_change(repo: Path) -> None:
    (repo / "head-change.txt").write_text("new commit while running\n", encoding="utf-8")
    result = _git(repo, "add", "head-change.txt")
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git add failed")
    result = _git(
        repo,
        "-c",
        "user.name=Codex Snooze v0.2",
        "-c",
        "user.email=codex-snooze-v02@example.invalid",
        "commit",
        "-q",
        "-m",
        "head change",
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git commit failed")


def build_payload(repetitions: int = 1) -> Dict[str, Any]:
    if repetitions <= 0:
        raise ValueError("repetitions must be positive")
    cases: List[Dict[str, Any]] = []
    for run_number in range(1, repetitions + 1):
        with tempfile.TemporaryDirectory(prefix="codex-snooze-v02-stale-") as temporary:
            temporary_path = Path(temporary)
            for name, mutate in (
                ("tracked_file_modified", _tracked_change),
                ("untracked_file_added", _untracked_add),
                ("branch_changed", _branch_change),
                ("head_changed", _head_change),
            ):
                case = _run_case(name, mutate, temporary_path)
                case["run_number"] = run_number
                cases.append(case)
    statuses = [case.get("status") for case in cases]
    aggregate = "PASS" if statuses and all(status == "PASS" for status in statuses) else "FAIL"
    return {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repetitions": repetitions,
        "cases": cases,
        "aggregate_status": aggregate,
        "contract": "successful jobs with tracked, untracked, branch or HEAD changes are COMPLETED_STALE",
    }


def write_outputs(payload: Dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "project-stale-tests.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    lines = [
        "# v0.2 project stale-state probe",
        "",
        f"Generated: `{payload['generated_at']}`",
        f"Repetitions: `{payload['repetitions']}`",
        f"Aggregate: **{payload['aggregate_status']}**",
        "",
        "| Mutation while job runs | Runs | PASS | PARTIAL | UNKNOWN | FAIL |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    names = sorted({case["name"] for case in payload["cases"]})
    for name in names:
        cases = [case for case in payload["cases"] if case["name"] == name]
        counts = {status: sum(case["status"] == status for case in cases) for status in ("PASS", "PARTIAL", "UNKNOWN", "FAIL")}
        lines.append(
            f"| `{name}` | {len(cases)} | {counts['PASS']} | {counts['PARTIAL']} | {counts['UNKNOWN']} | {counts['FAIL']} |"
        )
    lines.extend(
        [
            "",
            "Each case used a disposable Git repository and required exit code 0, "
            "source_state_changed=true, and COMPLETED_STALE.",
            "",
        ]
    )
    md = OUT / "project-stale-tests.md"
    md.write_text("\n".join(lines), encoding="utf-8")
    os.chmod(md, 0o600)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Probe Git stale-state handling")
    parser.add_argument("--runs", type=int, default=1, help="repetitions of all Git mutation cases")
    args = parser.parse_args(argv)
    payload = build_payload(args.runs)
    write_outputs(payload)
    print(json.dumps({"aggregate_status": payload["aggregate_status"], "cases": len(payload["cases"])}))
    return 0 if payload["aggregate_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
