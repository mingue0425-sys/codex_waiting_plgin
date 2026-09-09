#!/usr/bin/env python3
from __future__ import annotations

"""Live v0.4 explicit-handoff E2E.

Each run creates a private workspace and a new Snooze-owned App Server
thread.  The model receives one deterministic command and is required to
execute it once.  The controller observes the structured marker, interrupts
only the current turn, waits on the durable result and starts the continuation
on the same thread.
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.4"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.agent import AgentController, AgentControllerError
from snooze_controller.app_server_process import AppServerProcess
from snooze_controller.handoff import DynamicHandoffTool, HandoffController, HandoffControllerError
from snooze_controller.thread_registry import ThreadRegistry
from snooze_core.models import utc_now
from snooze_core.store import JobStore
from tools.app_server_probe import redact, write_trace


DEVELOPER_INSTRUCTIONS = """Codex Snooze integration contract:
When explicitly instructed to run a long non-interactive command through Codex
Snooze, use the provided `codex-snooze handoff` command exactly once. When it
reports a structured DETACHED marker, do not rerun the command and do not poll
the job manually. The controller will close this turn and create a continuation
turn after the saved result is available. Continue the original task only from
that completion event.
"""


def _copy_fixture(workspace: Path, fixture_name: str) -> None:
    fixture_dir = workspace / "fixtures"
    fixture_dir.mkdir()
    shutil.copy2(ROOT / "probes" / "fixtures" / "handoff_long_job.py", fixture_dir / "handoff_long_job.py")
    if fixture_name != "handoff_long_job.py":
        shutil.copy2(ROOT / "probes" / "fixtures" / fixture_name, fixture_dir / fixture_name)


def _prepare_git_fixture(workspace: Path) -> None:
    (workspace / ".gitignore").write_text(
        "run_count.txt\nresult_token.txt\nfixture_result.json\n__pycache__/\n",
        encoding="utf-8",
    )
    (workspace / "source.txt").write_text("source-before\n", encoding="utf-8")
    commands = [
        ["git", "init", "-q"],
        ["git", "config", "user.email", "codex-snooze-fixture@example.invalid"],
        ["git", "config", "user.name", "Codex Snooze Fixture"],
        ["git", "add", "."],
        ["git", "commit", "-qm", "fixture baseline"],
    ]
    for command in commands:
        subprocess.run(command, cwd=workspace, check=True, capture_output=True, text=True)


def _contains(value: Any, needle: str) -> bool:
    if isinstance(value, str):
        return needle in value
    if isinstance(value, dict):
        return any(_contains(item, needle) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains(item, needle) for item in value)
    return False


def _scrub(value: Any, root: Path) -> Any:
    root_texts = {str(root), str(root.resolve())}
    if str(root).startswith("/") and not str(root).startswith("/private/"):
        root_texts.add("/private" + str(root))
    project_texts = {str(ROOT), str(ROOT.resolve())}
    if isinstance(value, dict):
        return {str(key): _scrub(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item, root) for item in value]
    if isinstance(value, str):
        scrubbed = value
        for candidate in sorted(root_texts, key=len, reverse=True):
            scrubbed = scrubbed.replace(candidate, "<v04-fixture-root>")
        for candidate in sorted(project_texts, key=len, reverse=True):
            scrubbed = scrubbed.replace(candidate, "<project-root>")
        return re.sub(r"SNOOZE_[A-Z0-9_]+", "<redacted-token>", scrubbed)
    return value


def _run_once(
    *,
    threshold: float,
    duration: float,
    run_number: int,
    approval_policy: str,
    fixture_name: str = "handoff_long_job.py",
    expected_exit_code: int = 0,
    stale_mutation: bool = False,
) -> Dict[str, Any]:
    started = time.monotonic()
    token = f"SNOOZE_E2E_V04_{run_number:02d}_{int(time.time_ns())}"
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v04-e2e-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        _copy_fixture(workspace, fixture_name)
        if stale_mutation:
            _prepare_git_fixture(workspace)
        store_path = root / "store"
        store = JobStore(store_path)
        registry = ThreadRegistry(root / "threads.json")
        process = AppServerProcess(experimental_api=True)
        handoff_tool = DynamicHandoffTool(
            store,
            cwd=workspace,
            process=process,
            sandbox_policy={"type": "workspaceWrite", "writableRoots": [str(workspace)]},
        )
        process.request_handler = handoff_tool
        controller = AgentController(registry, cwd=workspace, process=process)
        handoff = HandoffController(controller, store, registry, poll_interval=0.05)
        first_turn: Optional[Dict[str, Any]] = None
        first_turn_observed: Optional[Dict[str, Any]] = None
        observation = None
        continuation: Optional[Dict[str, Any]] = None
        result: Optional[Dict[str, Any]] = None
        handoff_mapping: Optional[Dict[str, Any]] = None
        token_usage_during_wait: List[Dict[str, Any]] = []
        error: Optional[str] = None
        first_turn_status: Optional[str] = None
        marker_seen_elapsed: Optional[float] = None
        model_events: List[Dict[str, Any]] = []
        result_token: Optional[str] = None
        run_count: Optional[int] = None
        final_has_token = False
        final_failure_status = False
        final_stale_status = False
        same_thread = False
        job_survived_turn_end = False
        no_model_activity = False
        rerun_guard = False
        mapping_item_ok = False
        threshold_ok = False
        detached = False
        try:
            controller.start(timeout=30)
            controller.create_thread(
                cwd=workspace,
                sandbox="workspace-write",
                approval_policy=approval_policy,
                developer_instructions=DEVELOPER_INSTRUCTIONS,
                dynamic_tools=[DynamicHandoffTool.spec()],
                timeout=30,
            )
            command_args = [
                "python3",
                f"fixtures/{fixture_name}",
                "--duration",
                str(duration),
                "--run-count",
                "run_count.txt",
                "--result-token",
                "result_token.txt",
                "--result-json",
                "fixture_result.json",
                "--token",
                token,
                "--exit-code",
                str(expected_exit_code),
            ]
            command = shlex.join(
                [
                    str(ROOT / "scripts" / "codex-snooze"),
                    "--store",
                    str(store_path),
                    "handoff",
                    "--threshold",
                    str(threshold),
                    "--cwd",
                    str(workspace),
                    "--",
                    *command_args,
                ]
            )
            task = (
                "Use the named dynamic tool `codex_snooze_handoff` exactly once. "
                "Do not use the terminal tool for this step. Pass this exact argv vector as its command argument:\n"
                f"{json.dumps(command_args, ensure_ascii=False)}\n"
                f"with threshold_seconds={threshold} and cwd={workspace}. "
                f"The equivalent explicit command is: {command}\n"
                "Do not replace it with another command, do not simulate output, and do not run a second command. "
                "When the tool returns the structured DETACHED marker, stop this turn; the controller will "
                "continue the task after the job result is durable."
            )
            turn_started = time.monotonic()
            first_turn_info = controller.start_turn(task, timeout=60, cwd=workspace, approval_policy=approval_policy)
            first_turn = {"id": first_turn_info.get("id")}
            observation = handoff.wait_for_handoff(str(first_turn_info["id"]), timeout=max(90.0, threshold + 60.0))
            if observation.marker is not None:
                marker_seen_elapsed = time.monotonic() - turn_started
                if stale_mutation:
                    # The marker proves that the supervisor handed ownership
                    # off. Change a tracked fixture file while the child is
                    # still running; the result must carry COMPLETED_STALE.
                    (workspace / "source.txt").write_text("source-changed-during-job\n", encoding="utf-8")
                result = handoff.wait_for_job(observation, timeout=max(90.0, duration + 60.0))
                first_turn_observed = dict(observation.turn or {})
                handoff_mapping = registry.find_handoff(thread_id=controller.thread_id, job_id=str(result.get("job_id")))
                result_token = (workspace / "result_token.txt").read_text(encoding="utf-8").strip() if (workspace / "result_token.txt").exists() else None
                event_id = result.get("completion_event_id")
                continuation_prompt = (
                    "Continue the original handoff task from the completion event. Do not execute the completed "
                    "command again. Read the saved result token file with the terminal exactly once if needed and "
                    f"include the exact text RESULT_TOKEN={result_token or token} in your final response. "
                    f"The completion event id is {event_id}; recorded exit_code={result.get('exit_code')} "
                    f"execution_state={result.get('execution_state')}."
                )
                if expected_exit_code != 0:
                    continuation_prompt += " Include the exact line FAILURE_STATUS=EXIT_7 and explain that the job failed."
                if stale_mutation:
                    continuation_prompt += (
                        " The result has source_state_changed=true / COMPLETED_STALE. Include the exact line "
                        "SOURCE_STATUS=STALE_NOT_VERIFIED and do not claim that the current source tree was verified."
                    )
                routed = handoff.continue_after_job(observation, prompt=continuation_prompt, wait_timeout=180)
                continuation = routed.get("turn")
                run_count = int((workspace / "run_count.txt").read_text(encoding="utf-8").strip()) if (workspace / "run_count.txt").exists() else None
                final_has_token = _contains(continuation, f"RESULT_TOKEN={result_token or token}")
                final_failure_status = expected_exit_code == 0 or _contains(continuation, "FAILURE_STATUS=EXIT_7")
                final_stale_status = not stale_mutation or _contains(continuation, "SOURCE_STATUS=STALE_NOT_VERIFIED")
                model_events = HandoffController.model_activity_between(
                    controller.process,
                    observation.handoff_monotonic_ns,
                    observation.job_completed_monotonic_ns,
                )
                interval_events = [
                    event
                    for event in controller.process.events
                    if observation.handoff_monotonic_ns is not None
                    and observation.job_completed_monotonic_ns is not None
                    and observation.handoff_monotonic_ns <= int(event.get("monotonic_ns", 0)) <= observation.job_completed_monotonic_ns
                ]
                token_usage_during_wait = [
                    event for event in interval_events if event.get("method") == "thread/tokenUsage/updated"
                ]
                first_turn_status = first_turn_observed.get("status")
                detached = bool(observation.marker.get("state") == "DETACHED")
                threshold_ok = detached and float(observation.marker.get("threshold_seconds", -1)) == float(threshold) and marker_seen_elapsed >= max(0.0, threshold - 0.75)
                same_thread = controller.thread_id == registry.find_handoff(thread_id=controller.thread_id, job_id=str(result.get("job_id"))).get("thread_id") if registry.find_handoff(thread_id=controller.thread_id, job_id=str(result.get("job_id"))) else False
                job_survived_turn_end = bool(
                    first_turn_status in {"interrupted", "completed"}
                    and result.get("exit_code") == expected_exit_code
                )
                no_model_activity = len(model_events) == 0
                rerun_guard = run_count == 1
                mapping_item_ok = bool((handoff_mapping or {}).get("item_id"))
                complete = all((
                    same_thread,
                    detached,
                    mapping_item_ok,
                    threshold_ok,
                    job_survived_turn_end,
                    no_model_activity,
                    observation.phase.value == "CONTINUATION_COMPLETED",
                    final_has_token,
                    rerun_guard,
                    result.get("execution_state") == ("COMPLETED_STALE" if stale_mutation else "COMPLETED" if expected_exit_code == 0 else "FAILED"),
                    (not stale_mutation or bool((result or {}).get("source_state_changed"))),
                    final_failure_status,
                    final_stale_status,
                ))
                status = "PASS" if complete else "FAIL"
            else:
                marker_seen_elapsed = None
                status = "FAIL"
                first_turn_status = (observation.turn or {}).get("status")
                first_turn_observed = dict(observation.turn or {})
                model_events = []
                token_usage_during_wait = []
                result_token = None
                run_count = None
                final_has_token = False
                same_thread = False
                job_survived_turn_end = False
                no_model_activity = False
                rerun_guard = False
                mapping_item_ok = False
                threshold_ok = False
                detached = False
        except (AgentControllerError, HandoffControllerError, OSError, ValueError, RuntimeError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            status = "UNKNOWN" if observation is None else "FAIL"
            marker_seen_elapsed = None
            first_turn_status = (observation.turn or {}).get("status") if observation else None
            first_turn_observed = dict(observation.turn or {}) if observation else None
            model_events = []
            token_usage_during_wait = []
            result_token = None
            run_count = None
            final_has_token = False
            same_thread = False
            job_survived_turn_end = False
            no_model_activity = False
            rerun_guard = False
            mapping_item_ok = False
            threshold_ok = False
            detached = False
        finally:
            snapshot = controller.snapshot()
            controller.process.stop()
        handoff_mapping = handoff_mapping or registry.find_handoff(thread_id=controller.thread_id)
        value = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "status": status,
            "live_requested": True,
            "run_number": run_number,
            "thread_id": controller.thread_id,
            "first_turn_id": (first_turn or {}).get("id"),
            "first_turn_status": first_turn_status,
            "first_turn": first_turn_observed,
            "handoff": observation.as_dict() if observation else None,
            "handoff_mapping": handoff_mapping,
            "job_result": result,
            "marker_seen_elapsed_seconds": marker_seen_elapsed,
            "explicit_handoff": detached,
            "ten_second_handoff": threshold_ok,
            "job_survives_turn_end": job_survived_turn_end,
            "model_activity_during_wait": model_events,
            "model_events_during_long_wait": len(model_events),
            "no_model_activity_during_wait": no_model_activity,
            "token_usage_notifications_during_wait": len(token_usage_during_wait),
            "same_thread_continuation": same_thread,
            "continuation_turn": continuation,
            "final_contains_result_token": final_has_token,
            "final_contains_failure_status": final_failure_status,
            "final_contains_stale_status": final_stale_status,
            "result_token": result_token,
            "run_count": run_count,
            "rerun_guard": rerun_guard,
            "expected_exit_code": expected_exit_code,
            "expected_fixture_state": "COMPLETED_STALE" if stale_mutation else "COMPLETED" if expected_exit_code == 0 else "FAILED",
            "stale_mutation": stale_mutation,
            "event_to_job_mapping": mapping_item_ok,
            "dynamic_tool_calls": handoff_tool.calls,
            "app_server_snapshot": snapshot,
            "error": error,
            "duration_seconds": round(time.monotonic() - started, 3),
        }
        return _scrub(redact(value), root)


def run_live(
    runs: int,
    *,
    threshold: float,
    duration: float,
    approval_policy: str,
    fixture_name: str = "handoff_long_job.py",
    expected_exit_code: int = 0,
    stale_mutation: bool = False,
) -> Dict[str, Any]:
    if runs <= 0:
        raise ValueError("runs must be positive")
    results = [
        _run_once(
            threshold=threshold,
            duration=duration,
            run_number=index + 1,
            approval_policy=approval_policy,
            fixture_name=fixture_name,
            expected_exit_code=expected_exit_code,
            stale_mutation=stale_mutation,
        )
        for index in range(runs)
    ]
    required = [
        "explicit_handoff",
        "ten_second_handoff",
        "job_survives_turn_end",
        "no_model_activity_during_wait",
        "same_thread_continuation",
        "final_contains_result_token",
        "rerun_guard",
    ]
    if expected_exit_code != 0:
        required.append("final_contains_failure_status")
    if stale_mutation:
        required.append("final_contains_stale_status")
    passed = sum(item.get("status") == "PASS" for item in results)
    aggregate = "PASS" if passed == runs else "FAIL" if any(item.get("status") == "FAIL" for item in results) else "UNKNOWN"
    return {
        "schema_version": 1,
        "generated_at": utc_now(),
        "status": aggregate,
        "runs": runs,
        "passed_runs": passed,
        "threshold_seconds": threshold,
        "fixture_duration_seconds": duration,
        "approval_policy": approval_policy,
        "fixture_name": fixture_name,
        "expected_exit_code": expected_exit_code,
        "stale_mutation": stale_mutation,
        "required_conditions": list(required),
        "results": results,
        "analysis": (
            "All explicit handoff, turn-close, OS-only wait, completion and same-thread continuation conditions passed."
            if aggregate == "PASS"
            else "The run did not prove the complete handoff chain. Inspect the per-run event, marker and continuation fields; no automatic path is enabled."
        ),
    }


def markdown(value: Dict[str, Any]) -> str:
    lines = [
        "# v0.4 explicit handoff E2E",
        "",
        f"Generated: `{value['generated_at']}`",
        f"Status: **{value['status']}**",
        f"Runs: `{value['passed_runs']}/{value['runs']}`",
        "",
        value["analysis"],
        "",
        "| Condition | All runs |",
        "|---|---|",
    ]
    for condition in value["required_conditions"]:
        values = [bool(item.get(condition)) for item in value["results"]]
        lines.append(f"| `{condition}` | `{all(values)}` |")
    lines.extend(["", "| Run | Status | Thread | Job | First turn |", "|---:|---|---|---|---|"])
    for item in value["results"]:
        job = (item.get("job_result") or {}).get("job_id")
        lines.append(f"| {item.get('run_number')} | {item.get('status')} | `{item.get('thread_id')}` | `{job}` | `{item.get('first_turn_id')}` |")
    lines.extend([
        "",
        "The fixture records a run counter and a result token. A PASS requires",
        "the same durable thread, a single fixture run, a durable completion",
        "event and the exact token in the continuation turn.",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="run the model-backed App Server experiment")
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--threshold", type=float, default=10.0)
    parser.add_argument("--duration", type=float, default=12.0)
    parser.add_argument("--approval-policy", choices=("never", "on-request"), default="never")
    parser.add_argument("--fixture", default="handoff_long_job.py")
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--stale-mutation", action="store_true")
    parser.add_argument("--output-stem", default="handoff-e2e")
    args = parser.parse_args()
    if not args.live:
        value = {"schema_version": 1, "generated_at": utc_now(), "status": "UNKNOWN", "live_requested": False, "analysis": "Run with --live for the model-backed E2E."}
    else:
        value = run_live(
            args.runs,
            threshold=args.threshold,
            duration=args.duration,
            approval_policy=args.approval_policy,
            fixture_name=args.fixture,
            expected_exit_code=args.exit_code,
            stale_mutation=args.stale_mutation,
        )
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / f"{args.output_stem}.json", value)
    (OUT / f"{args.output_stem}.md").write_text(markdown(value), encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value.get("status") == "PASS" else 1 if args.live and value.get("status") == "FAIL" else 2 if args.live else 0


if __name__ == "__main__":
    raise SystemExit(main())
