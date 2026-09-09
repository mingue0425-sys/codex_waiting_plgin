#!/usr/bin/env python3
from __future__ import annotations

"""Separate App Server crash/job survival and durable thread reconnect tests."""

import json
import os
import signal
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.3"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.agent import AgentController, AgentControllerError
from snooze_controller.app_server_process import AppServerProcess
from snooze_controller.native_backend import NativeAppServerBackend
from snooze_controller.thread_registry import ThreadRegistry
from tools.app_server_probe import redact, utc_now, write_trace


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v03-crash-") as temporary:
        root = Path(temporary)
        marker = root / "survival-marker"
        process = AppServerProcess(cwd=ROOT, experimental_api=True)
        job_survival = "UNKNOWN"
        job_error = None
        handle = None
        crash_state = "UNKNOWN"
        try:
            process.start(timeout=30)
            backend = NativeAppServerBackend(process)
            handle = backend.start(
                [
                    sys.executable,
                    "-c",
                    f"import time; from pathlib import Path; time.sleep(2); Path({str(marker)!r}).write_text('SURVIVED')",
                ],
                cwd=root,
                timeout_ms=15000,
            )
            server_pid = process.pid
            if server_pid is None:
                raise RuntimeError("missing App Server pid")
            os.kill(server_pid, signal.SIGKILL)
            try:
                process.process.wait(timeout=5) if process.process is not None else None
            except (OSError, AttributeError):
                pass
            time.sleep(3)
            job_survival = "PASS" if marker.exists() else "FAIL"
            crash_state = process.state.value
        except (OSError, RuntimeError, ValueError) as exc:
            job_error = f"{type(exc).__name__}: {exc}"
        finally:
            process.stop()

        registry = ThreadRegistry(root / "reconnect-threads.json")
        first = AppServerProcess(cwd=ROOT)
        controller = AgentController(registry, process=first)
        thread_id = None
        first_turn = None
        resume_status = "UNKNOWN"
        resume_error = None
        try:
            first.start(timeout=30)
            controller.create_thread(cwd=root, sandbox="workspace-write", approval_policy="never", timeout=30)
            thread_id = controller.thread_id
            turn = controller.start_turn("Reply with exactly the word RECONNECT_READY. Do not use tools.", timeout=30)
            first_turn = controller.wait_turn(turn["id"], timeout=120)
        except (OSError, RuntimeError, AgentControllerError) as exc:
            resume_error = f"initial turn: {type(exc).__name__}: {exc}"
        finally:
            first.stop()
        second = AppServerProcess(cwd=ROOT)
        try:
            second.start(timeout=30)
            if thread_id:
                resumed = second.request("thread/resume", {"threadId": thread_id, "excludeTurns": True}, timeout=30)
                resume_status = "PASS" if resumed.get("error") is None else "UNKNOWN"
                if resumed.get("error") is not None:
                    resume_error = str(resumed.get("error"))
        except (OSError, RuntimeError) as exc:
            resume_error = f"reconnect: {type(exc).__name__}: {exc}"
        finally:
            second.stop()
        value: Dict[str, Any] = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "app_server_crash": {
                "status": "PASS" if crash_state == "CRASHED" else "UNKNOWN",
                "state_after_kill": crash_state,
                "job_survives_app_server_loss": job_survival,
                "job_error": job_error,
            },
            "durable_thread_reconnect": {
                "status": resume_status,
                "thread_id": thread_id,
                "initial_turn": first_turn,
                "error": resume_error,
                "conversation_restored": resume_status == "PASS",
                "live_tool_state_restored": "UNKNOWN",
                "background_job_linkage_restored": "UNKNOWN",
            },
            "overall": "PASS" if crash_state == "CRASHED" and job_survival == "PASS" and resume_status == "PASS" else "PARTIAL" if job_survival != "FAIL" and resume_status != "FAIL" else "FAIL",
            "exactly_once": "NOT_CLAIMED",
        }
    OUT.mkdir(parents=True, exist_ok=True)
    value = redact(value)
    write_trace(OUT / "crash-reconnect.json", value)
    (OUT / "crash-reconnect.md").write_text(
        "# v0.3 App Server crash and reconnect\n\n"
        f"Overall: **{value['overall']}**\n\n"
        f"App Server crash/job result: **{value['app_server_crash']['job_survives_app_server_loss']}**\n\n"
        f"Durable thread resume: **{value['durable_thread_reconnect']['status']}**\n\n"
        "A restored conversation does not imply restored live tool state or a "
        "background job linkage. Those are reported independently.\n",
        encoding="utf-8",
    )
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
