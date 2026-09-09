#!/usr/bin/env python3
from __future__ import annotations

"""Probe busy-thread delivery candidates and the idle-check TOCTOU race.

All mutations are confined to disposable App Server threads.  An accepted
request is reported separately from a safe completion-delivery verdict: a
method accepting a message does not prove that the message is an idempotent
completion event or that the Desktop UI will render it.
"""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.app_server_probe import AppServerClient, redact, response_error, write_trace


OUT = ROOT / "results" / "v0.2"
MECHANISMS = (
    "turn/start.toolOutput",
    "turn/steer",
    "thread/inject_items",
    "thread/queue/add",
    "thread/resume+turn/start",
    "codex-exec-resume",
)


def summarize(response: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if response is None:
        return {"error": "no response"}
    return {"error": response_error(response), "response": redact(response)}


def initialize(client: AppServerClient) -> Dict[str, Any]:
    response = client.request(
        "initialize",
        {
            "clientInfo": {"name": "codex-snooze-v02-concurrency", "version": "0.2.0"},
            "capabilities": {"experimentalApi": True, "requestAttestation": False},
        },
        timeout=20,
    )
    if response_error(response) is None:
        client.notify("initialized", {})
    return response


def start_thread(client: AppServerClient) -> tuple[Optional[str], Dict[str, Any]]:
    response = client.request("thread/start", {"cwd": str(ROOT), "ephemeral": False}, timeout=20)
    thread = ((response.get("result") or {}).get("thread") or {}) if response_error(response) is None else {}
    return thread.get("id"), response


def start_busy_turn(client: AppServerClient, thread_id: str) -> tuple[Optional[str], Dict[str, Any]]:
    response = client.request(
        "turn/start",
        {
            "threadId": thread_id,
            "input": [
                {
                    "type": "text",
                    "text": (
                        "Use the terminal tool immediately. Run exactly `/bin/sh -c 'sleep 3'`, wait for it to "
                        "finish, and do not answer before the command completes."
                    ),
                }
            ],
        },
        timeout=30,
    )
    turn = ((response.get("result") or {}).get("turn") or {}) if response_error(response) is None else {}
    return turn.get("id"), response


def wait_turn_completed(client: AppServerClient, turn_id: Optional[str], timeout: float = 30.0) -> Optional[Dict[str, Any]]:
    if not turn_id:
        return None
    return client.wait_for_notification(
        lambda item: item.get("method") == "turn/completed"
        and ((item.get("params") or {}).get("turn") or {}).get("id") == turn_id,
        timeout=timeout,
    )


def interrupt(client: AppServerClient, thread_id: Optional[str], turn_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not thread_id or not turn_id:
        return None
    try:
        return client.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=20)
    except (OSError, RuntimeError, TimeoutError, ValueError):
        return None


def mechanism_probe(mechanism: str, run_number: int, live: bool) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "mechanism": mechanism,
        "run_number": run_number,
        "live_requested": live,
        "status": "UNKNOWN",
        "safe_completion_delivery": "UNKNOWN",
    }
    if not live:
        record["reason"] = "live model probe not requested"
        return record
    if mechanism == "codex-exec-resume":
        record["reason"] = "not attempted: no verified mapping from disposable App Server thread to Desktop CLI session"
        return record

    client = AppServerClient(cwd=ROOT)
    thread_id: Optional[str] = None
    active_turn_id: Optional[str] = None
    try:
        init = initialize(client)
        record["initialize"] = summarize(init)
        if response_error(init):
            record["reason"] = "initialize failed"
            return record
        thread_id, started = start_thread(client)
        record["thread_start"] = summarize(started)
        record["thread_id"] = thread_id
        if not thread_id:
            record["reason"] = "disposable thread was not created"
            return record

        if mechanism == "thread/resume+turn/start":
            before = client.request(
                "thread/resume", {"threadId": thread_id, "excludeTurns": True}, timeout=20
            )
            record["resume"] = summarize(before)
            if response_error(before):
                record["status"] = "PARTIAL"
                record["reason"] = "resume before a rollout is rejected by the installed server"
                return record
            response = client.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": "Reply with exactly V02_RESUMED_TURN."}],
                },
                timeout=30,
            )
            record["turn_start_after_resume"] = summarize(response)
            turn = ((response.get("result") or {}).get("turn") or {}) if response_error(response) is None else {}
            active_turn_id = turn.get("id")
            completed = wait_turn_completed(client, active_turn_id, 45)
            record["turn_completed"] = redact(completed)
            record["status"] = "PASS" if response_error(response) is None and completed is not None else "PARTIAL"
            record["safe_completion_delivery"] = "UNKNOWN"
            return record

        active_turn_id, turn_response = start_busy_turn(client, thread_id)
        record["active_turn"] = summarize(turn_response)
        record["active_turn_id"] = active_turn_id
        time.sleep(0.15)
        record["active_status_notifications"] = [
            redact(item)
            for item in client.notifications
            if item.get("method") == "thread/status/changed"
        ][-5:]
        marker = f"V02_COMPLETION_{mechanism.replace('/', '_')}_{uuid.uuid4().hex}"
        if mechanism == "turn/steer":
            delivery_response = client.request(
                "turn/steer",
                {
                    "threadId": thread_id,
                    "expectedTurnId": active_turn_id,
                    "input": [{"type": "text", "text": marker}],
                },
                timeout=20,
            )
        elif mechanism == "thread/inject_items":
            delivery_response = client.request(
                "thread/inject_items",
                {
                    "threadId": thread_id,
                    "items": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": marker}],
                        }
                    ],
                },
                timeout=20,
            )
        elif mechanism == "thread/queue/add":
            delivery_response = client.request(
                "thread/queue/add",
                {
                    "threadId": thread_id,
                    "clientUserMessageId": str(uuid.uuid4()),
                    "input": [{"type": "text", "text": marker}],
                },
                timeout=20,
            )
            queued = ((delivery_response.get("result") or {}).get("queuedSubmission") or {})
            queued_id = queued.get("id") if isinstance(queued, dict) else None
            record["queued_submission_id"] = queued_id
            queue_list = client.request(
                "thread/queue/list", {"threadId": thread_id, "limit": 20}, timeout=20
            )
            record["queue_list"] = summarize(queue_list)
            if queued_id:
                deleted = client.request(
                    "thread/queue/delete",
                    {"threadId": thread_id, "queuedSubmissionId": queued_id},
                    timeout=20,
                )
                record["queue_delete"] = summarize(deleted)
        elif mechanism == "turn/start.toolOutput":
            delivery_response = client.request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [],
                    "toolOutput": {"name": "codex_snooze_completion", "output": marker},
                },
                timeout=20,
            )
        else:
            raise ValueError(f"unknown mechanism {mechanism}")
        record["delivery_response"] = summarize(delivery_response)
        accepted = response_error(delivery_response) is None
        record["accepted_while_turn_expected_active"] = accepted
        record["safe_completion_delivery"] = "UNKNOWN"
        record["status"] = "PASS" if accepted else "PARTIAL"
        completed = wait_turn_completed(client, active_turn_id, timeout=30)
        record["active_turn_completed"] = redact(completed)
        return record
    except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
        record["status"] = "UNKNOWN"
        record["reason"] = f"{type(exc).__name__}: {exc}"
        return record
    finally:
        interrupt_response = interrupt(client, thread_id, active_turn_id)
        if interrupt_response is not None:
            record["cleanup_interrupt"] = summarize(interrupt_response)
        if thread_id:
            try:
                client.request("thread/delete", {"threadId": thread_id}, timeout=10)
            except (OSError, RuntimeError, TimeoutError, ValueError):
                pass
        record["notification_methods"] = [item.get("method") for item in client.notifications]
        record["trace"] = client.snapshot()
        client.close()


def toc_race(run_number: int, live: bool) -> Dict[str, Any]:
    record: Dict[str, Any] = {
        "name": "idle_check_then_external_turn",
        "run_number": run_number,
        "live_requested": live,
        "status": "UNKNOWN",
    }
    if not live:
        record["reason"] = "live model probe not requested"
        return record
    user = AppServerClient(cwd=ROOT)
    controller = AppServerClient(cwd=ROOT)
    thread_id: Optional[str] = None
    user_turn_id: Optional[str] = None
    controller_turn_id: Optional[str] = None
    try:
        user_init = initialize(user)
        controller_init = initialize(controller)
        record["user_initialize"] = summarize(user_init)
        record["controller_initialize"] = summarize(controller_init)
        thread_id, started = start_thread(user)
        record["thread_start"] = summarize(started)
        if not thread_id:
            record["reason"] = "disposable thread was not created"
            return record
        warmup_turn_id, warmup_response = start_busy_turn(user, thread_id)
        record["warmup_turn_start"] = summarize(warmup_response)
        warmup_completed = wait_turn_completed(user, warmup_turn_id, 45)
        record["warmup_turn_completed"] = redact(warmup_completed)
        idle_read = controller.request(
            "thread/read", {"threadId": thread_id, "includeTurns": False}, timeout=20
        )
        record["idle_read"] = summarize(idle_read)
        if response_error(idle_read) is not None:
            resume = controller.request(
                "thread/resume", {"threadId": thread_id, "excludeTurns": True}, timeout=20
            )
            record["controller_resume"] = summarize(resume)
            if response_error(resume) is None:
                idle_read = resume
        idle_status = (((idle_read.get("result") or {}).get("thread") or {}).get("status") or {}).get("type")
        record["idle_status_observed"] = idle_status
        user_turn, user_turn_response = start_busy_turn(user, thread_id)
        user_turn_id = user_turn
        record["user_turn_start"] = summarize(user_turn_response)
        time.sleep(0.1)
        controller_turn_response = controller.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": "Reply with exactly V02_CONTROLLER_RACE."}],
            },
            timeout=20,
        )
        record["controller_turn_start"] = summarize(controller_turn_response)
        controller_turn = ((controller_turn_response.get("result") or {}).get("turn") or {})
        controller_turn_id = controller_turn.get("id") if response_error(controller_turn_response) is None else None
        record["controller_turn_id"] = controller_turn_id
        if idle_status == "idle" and user_turn_id and response_error(user_turn_response) is None:
            if controller_turn_id is None and response_error(controller_turn_response) is not None:
                record["status"] = "PASS"
                record["concurrent_turn_accepted"] = False
            elif controller_turn_id is not None:
                record["status"] = "FAIL"
                record["concurrent_turn_accepted"] = True
            else:
                record["status"] = "PARTIAL"
        else:
            record["status"] = "UNKNOWN"
            record["reason"] = "idle state or user active turn could not be established"
        return record
    except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
        record["status"] = "UNKNOWN"
        record["reason"] = f"{type(exc).__name__}: {exc}"
        return record
    finally:
        interrupt(user, thread_id, user_turn_id)
        interrupt(controller, thread_id, controller_turn_id)
        wait_turn_completed(user, user_turn_id, timeout=10)
        wait_turn_completed(controller, controller_turn_id, timeout=10)
        if thread_id:
            try:
                user.request("thread/delete", {"threadId": thread_id}, timeout=10)
            except (OSError, RuntimeError, TimeoutError, ValueError):
                pass
        record["user_notification_methods"] = [item.get("method") for item in user.notifications]
        record["controller_notification_methods"] = [item.get("method") for item in controller.notifications]
        record["user_trace"] = user.snapshot()
        record["controller_trace"] = controller.snapshot()
        user.close()
        controller.close()


def build_payload(live: bool, repetitions: int) -> Dict[str, Any]:
    mechanisms = [mechanism_probe(name, run_number, live) for name in MECHANISMS for run_number in range(1, repetitions + 1)]
    toc = [toc_race(run_number, live) for run_number in range(1, repetitions + 1)]
    busy_statuses = [item["status"] for item in mechanisms if item["mechanism"] != "codex-exec-resume"]
    if not live:
        busy_status = "UNKNOWN"
    elif busy_statuses and all(status == "PASS" for status in busy_statuses):
        busy_status = "PARTIAL"
    elif any(status in {"PASS", "PARTIAL"} for status in busy_statuses):
        busy_status = "PARTIAL"
    else:
        busy_status = "UNKNOWN"
    toc_statuses = [item["status"] for item in toc]
    toc_status = "PASS" if toc_statuses and all(status == "PASS" for status in toc_statuses) else "PARTIAL" if any(status == "PASS" for status in toc_statuses) else "UNKNOWN"
    return {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "live_requested": live,
        "repetitions": repetitions,
        "mechanisms": mechanisms,
        "idle_check_toc_tou": toc,
        "capabilities": {
            "busy_thread_delivery": busy_status,
            "idle_check_to_external_turn_race": toc_status,
            "automatic_completion_delivery": "UNKNOWN",
        },
        "interpretation": "Accepted App Server requests are not treated as safe completion injection without idempotency, ordering and Desktop UI evidence.",
    }


def write_outputs(payload: Dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "concurrency-tests.json", payload)
    lines = [
        "# v0.2 busy-thread and TOCTOU probes",
        "",
        f"Generated: `{payload['generated_at']}`",
        f"Live model probe: `{payload['live_requested']}`",
        f"Repetitions: `{payload['repetitions']}`",
        "",
        "| Capability | Status |",
        "|---|---|",
    ]
    for name, status in payload["capabilities"].items():
        lines.append(f"| `{name}` | **{status}** |")
    lines.extend(["", "## Mechanisms", "", "| Mechanism | PASS | PARTIAL | UNKNOWN |", "|---|---:|---:|---:|"])
    for mechanism in MECHANISMS:
        cases = [item for item in payload["mechanisms"] if item["mechanism"] == mechanism]
        lines.append(
            f"| `{mechanism}` | {sum(item['status'] == 'PASS' for item in cases)} | "
            f"{sum(item['status'] == 'PARTIAL' for item in cases)} | {sum(item['status'] == 'UNKNOWN' for item in cases)} |"
        )
    lines.extend(["", "## TOCTOU", ""])
    for case in payload["idle_check_toc_tou"]:
        lines.append(f"- run {case['run_number']}: **{case['status']}** — {case.get('reason', '')}")
    lines.extend(
        [
            "",
            "A successful request is recorded as API acceptance only. It does",
            "not establish safe completion delivery, exactly-once behavior, or",
            "Desktop UI visibility.",
            "",
        ]
    )
    (OUT / "concurrency-tests.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Probe App Server busy-thread delivery and TOCTOU races")
    parser.add_argument("--live", action="store_true", help="run disposable-thread model probes")
    parser.add_argument("--runs", type=int, default=1, help="repetitions per mechanism and TOCTOU case")
    args = parser.parse_args(argv)
    if args.runs <= 0:
        parser.error("--runs must be positive")
    payload = build_payload(args.live, args.runs)
    write_outputs(payload)
    print(json.dumps(payload["capabilities"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
