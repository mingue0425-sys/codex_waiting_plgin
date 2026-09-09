#!/usr/bin/env python3
from __future__ import annotations

"""Crash the owned App Server after handoff and reconnect the same thread."""

import json
import os
import shutil
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.4"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from probes.v0_4_handoff_e2e import DEVELOPER_INSTRUCTIONS, _contains, _scrub
from snooze_controller.agent import AgentController, AgentControllerError
from snooze_controller.app_server_process import AppServerLifecycle, AppServerProcess
from snooze_controller.handoff import DynamicHandoffTool, HandoffController, HandoffControllerError
from snooze_controller.thread_registry import ThreadRegistry
from snooze_core.models import utc_now
from snooze_core.store import JobStore
from tools.app_server_probe import redact, write_trace


def run() -> Dict[str, Any]:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v04-app-crash-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        fixture_dir = workspace / "fixtures"
        fixture_dir.mkdir()
        shutil.copy2(ROOT / "probes" / "fixtures" / "handoff_long_job.py", fixture_dir / "handoff_long_job.py")
        store = JobStore(root / "store")
        registry = ThreadRegistry(root / "threads.json")
        token = f"SNOOZE_V04_APP_CRASH_{time.time_ns()}"
        process = AppServerProcess(experimental_api=True)
        tool = DynamicHandoffTool(
            store,
            cwd=workspace,
            process=process,
            sandbox_policy={"type": "workspaceWrite", "writableRoots": [str(workspace)]},
        )
        process.request_handler = tool
        controller = AgentController(registry, cwd=workspace, process=process)
        handoff = HandoffController(controller, store, registry, poll_interval=0.05)
        observation = None
        result = None
        thread_id: Optional[str] = None
        first_turn: Optional[Dict[str, Any]] = None
        crash_state = "UNKNOWN"
        crash_error: Optional[str] = None
        resume_status = "UNKNOWN"
        continuation_status = "UNKNOWN"
        continuation: Optional[Dict[str, Any]] = None
        error: Optional[str] = None
        try:
            controller.start(timeout=30)
            controller.create_thread(
                cwd=workspace,
                sandbox="workspace-write",
                approval_policy="never",
                developer_instructions=DEVELOPER_INSTRUCTIONS,
                dynamic_tools=[DynamicHandoffTool.spec()],
                timeout=30,
            )
            thread_id = controller.thread_id
            command = [
                "python3",
                "fixtures/handoff_long_job.py",
                "--duration",
                "3",
                "--run-count",
                "run_count.txt",
                "--result-token",
                "result_token.txt",
                "--result-json",
                "fixture_result.json",
                "--token",
                token,
                "--exit-code",
                "0",
            ]
            task = (
                "Use the named dynamic tool codex_snooze_handoff exactly once with this exact argv vector: "
                + json.dumps(command)
                + " and threshold_seconds=0.5. Do not use the terminal tool, do not simulate output, and stop "
                "after the structured DETACHED marker."
            )
            turn = controller.start_turn(task, timeout=60, cwd=workspace, approval_policy="never")
            first_turn = {"id": turn.get("id")}
            observation = handoff.wait_for_handoff(str(turn["id"]), timeout=120)
            if not observation.marker:
                raise RuntimeError("no handoff marker before App Server crash")
            if process.pid is None:
                raise RuntimeError("owned App Server pid is unavailable")
            os.kill(process.pid, signal.SIGKILL)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and process.state not in {AppServerLifecycle.CRASHED, AppServerLifecycle.FAILED}:
                time.sleep(0.05)
            crash_state = process.state.value
            result = handoff.wait_for_job(observation, timeout=30)
        except (OSError, RuntimeError, ValueError, AgentControllerError, HandoffControllerError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            # stop only after recording the crash lifecycle state; this closes
            # pipes and removes the owned process without touching the job.
            try:
                process.stop()
            except (OSError, RuntimeError):
                pass

        job_survived = bool(result and result.get("execution_state") in {"COMPLETED", "COMPLETED_STALE"} and result.get("exit_code") == 0)
        if thread_id and result and observation is not None:
            second_process = AppServerProcess(experimental_api=True)
            second = AgentController(registry, cwd=workspace, process=second_process)
            second_handoff = HandoffController(second, store, registry, router_path=store.root / "app-server-completions.json", poll_interval=0.05)
            try:
                second.start(timeout=30)
                second.resume_thread(thread_id, timeout=30, cwd=workspace)
                resume_status = "PASS"
                result_token = (workspace / "result_token.txt").read_text(encoding="utf-8").strip()
                prompt = (
                    "Continue from the recorded completion event without rerunning the completed command. "
                    "Read result_token.txt exactly once with the terminal, then include the exact line "
                    f"RESULT_TOKEN={result_token} in your final response."
                )
                continuation_result = second_handoff.continue_after_job(
                    observation,
                    prompt=prompt,
                    wait_timeout=180,
                )
                continuation = continuation_result.get("turn")
                run_count = int((workspace / "run_count.txt").read_text(encoding="utf-8").strip())
                continuation_status = "PASS" if (
                    _contains(continuation, f"RESULT_TOKEN={result_token}") and run_count == 1
                ) else "FAIL"
            except (OSError, RuntimeError, ValueError, AgentControllerError, HandoffControllerError) as exc:
                resume_status = "UNKNOWN" if resume_status != "PASS" else resume_status
                continuation_status = "UNKNOWN"
                crash_error = f"{type(exc).__name__}: {exc}"
            finally:
                second_process.stop()
        value = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "status": "PASS" if crash_state == AppServerLifecycle.CRASHED.value and job_survived and resume_status == "PASS" and continuation_status == "PASS" else "UNKNOWN" if error or resume_status == "UNKNOWN" else "FAIL",
            "app_server_crash_detected": crash_state == AppServerLifecycle.CRASHED.value,
            "app_server_state_after_kill": crash_state,
            "job_survived_app_server_crash": job_survived,
            "durable_thread_resume": resume_status,
            "completion_after_reconnect": continuation_status,
            "thread_id": thread_id,
            "first_turn": first_turn,
            "handoff": observation.as_dict() if observation else None,
            "job_result": result,
            "continuation_turn": continuation,
            "error": error or crash_error,
            "exactly_once": "NOT_CLAIMED",
            "duration_seconds": round(time.monotonic() - started, 3),
        }
        return _scrub(redact(value), root)


def main() -> int:
    value = run()
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "app-server-crash.json", value)
    (OUT / "app-server-crash.md").write_text(
        "# v0.4 App Server crash during handoff\n\n"
        f"Status: **{value['status']}**\n\n"
        f"Job survived App Server crash: **{value['job_survived_app_server_crash']}**\n\n"
        f"Durable thread resume: **{value['durable_thread_resume']}**\n\n"
        f"Completion after reconnect: **{value['completion_after_reconnect']}**\n\n"
        "Exactly-once delivery is not claimed.\n",
        encoding="utf-8",
    )
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value["status"] == "PASS" else 1 if value["status"] == "FAIL" else 2


if __name__ == "__main__":
    raise SystemExit(main())
