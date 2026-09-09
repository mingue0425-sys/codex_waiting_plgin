#!/usr/bin/env python3
from __future__ import annotations

"""Crash the Snooze controller after a durable handoff checkpoint."""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.4"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.agent import AgentController, AgentControllerError
from snooze_controller.app_server_process import AppServerProcess
from probes.v0_4_handoff_e2e import _contains
from snooze_controller.handoff import HandoffController, HandoffControllerError, HandoffObservation, HandoffPhase
from snooze_controller.thread_registry import ThreadRegistry
from snooze_core.models import utc_now
from snooze_core.store import JobStore
from tools.app_server_probe import redact, write_trace


def run() -> Dict[str, Any]:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v04-controller-crash-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        fixture_dir = workspace / "fixtures"
        fixture_dir.mkdir(parents=True)
        shutil.copy2(ROOT / "probes" / "fixtures" / "handoff_long_job.py", fixture_dir / "handoff_long_job.py")
        store_path = root / "store"
        registry_path = root / "threads.json"
        checkpoint_path = root / "handoff-checkpoint.json"
        token = f"SNOOZE_V04_CONTROLLER_CRASH_{time.time_ns()}"
        worker = ROOT / "probes" / "fixtures" / "v0_4_controller_crash_worker.py"
        command = [
            sys.executable,
            str(worker),
            "--workspace",
            str(workspace),
            "--store",
            str(store_path),
            "--registry",
            str(registry_path),
            "--checkpoint",
            str(checkpoint_path),
            "--token",
            token,
        ]
        worker_result: Dict[str, Any]
        try:
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=150, check=False)
            worker_result = {
                "returncode": completed.returncode,
                "stdout": completed.stdout[-2000:],
                "stderr": completed.stderr[-2000:],
            }
        except (OSError, subprocess.TimeoutExpired) as exc:
            worker_result = {"returncode": 124, "error": f"{type(exc).__name__}: {exc}"}

        checkpoint: Optional[Dict[str, Any]] = None
        checkpoint_error = None
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            checkpoint_error = f"{type(exc).__name__}: {exc}"
        app_server_pid = (checkpoint or {}).get("app_server_pid")
        if isinstance(app_server_pid, int):
            try:
                os.kill(app_server_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                pass

        store = JobStore(store_path)
        job_id = str(((checkpoint or {}).get("marker") or {}).get("job_id") or "")
        result = None
        if job_id:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                result = store.read_result(job_id)
                if result is not None and result.get("execution_state") in {"COMPLETED", "COMPLETED_STALE", "FAILED", "CANCELLED"}:
                    break
                time.sleep(0.05)

        registry = ThreadRegistry(registry_path)
        thread_id = (checkpoint or {}).get("thread_id")
        resume_status = "UNKNOWN"
        continuation_status = "UNKNOWN"
        continuation = None
        error = checkpoint_error
        if thread_id and result:
            process = AppServerProcess(cwd=ROOT)
            controller = AgentController(registry, cwd=workspace, process=process)
            try:
                process.start(timeout=30)
                controller.resume_thread(str(thread_id), timeout=30, cwd=workspace)
                resume_status = "PASS"
                token_value = (workspace / "result_token.txt").read_text(encoding="utf-8").strip()
                observation = HandoffObservation(
                    phase=HandoffPhase.JOB_COMPLETED,
                    thread_id=str(thread_id),
                    turn_id=str((checkpoint or {}).get("turn_id") or ""),
                    marker=dict((checkpoint or {}).get("marker") or {}),
                    item_id=(checkpoint or {}).get("item_id"),
                    result=result,
                    turn=(checkpoint or {}).get("turn"),
                )
                handoff = HandoffController(
                    controller,
                    store,
                    registry,
                    router_path=store.root / "app-server-completions.json",
                    poll_interval=0.05,
                )
                routed = handoff.continue_after_job(
                    observation,
                    prompt=(
                        "Resume the original task from the completion event. Do not rerun the completed command. "
                        "Read result_token.txt exactly once and include the exact line "
                        f"RESULT_TOKEN={token_value} in your final response."
                    ),
                    wait_timeout=180,
                )
                continuation = routed.get("turn")
                run_count = int((workspace / "run_count.txt").read_text(encoding="utf-8").strip())
                continuation_status = "PASS" if _contains(continuation, f"RESULT_TOKEN={token_value}") and run_count == 1 else "FAIL"
            except (OSError, RuntimeError, ValueError, AgentControllerError, HandoffControllerError) as exc:
                error = f"{type(exc).__name__}: {exc}"
            finally:
                process.stop()
        job_status = bool(result and result.get("execution_state") in {"COMPLETED", "COMPLETED_STALE"} and result.get("exit_code") == 0)
        value = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "status": "PASS" if worker_result.get("returncode") == 73 and checkpoint and job_status and resume_status == "PASS" and continuation_status == "PASS" else "UNKNOWN" if error or resume_status == "UNKNOWN" else "FAIL",
            "controller_crashed": worker_result.get("returncode") == 73,
            "checkpoint_persisted": checkpoint is not None,
            "job_survived_controller_crash": job_status,
            "pending_completion_recovered": bool(result),
            "durable_thread_resume": resume_status,
            "completion_after_controller_restart": continuation_status,
            "worker": worker_result,
            "checkpoint": checkpoint,
            "job_result": result,
            "continuation_turn": continuation,
            "error": error,
            "exactly_once": "NOT_CLAIMED",
            "duration_seconds": round(time.monotonic() - started, 3),
        }
        # Do not retain the worker's private temp path or token-bearing prose.
        raw = redact(value)
        raw_text = json.dumps(raw, ensure_ascii=False)
        return json.loads(raw_text.replace(str(root), "<v04-controller-crash-root>"))


def main() -> int:
    value = run()
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "controller-crash.json", value)
    (OUT / "controller-crash.md").write_text(
        "# v0.4 controller crash recovery\n\n"
        f"Status: **{value['status']}**\n\n"
        f"Checkpoint persisted: **{value['checkpoint_persisted']}**\n\n"
        f"Job survived controller crash: **{value['job_survived_controller_crash']}**\n\n"
        f"Completion after restart: **{value['completion_after_controller_restart']}**\n\n"
        "Exactly-once delivery is not claimed.\n",
        encoding="utf-8",
    )
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value["status"] == "PASS" else 1 if value["status"] == "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
