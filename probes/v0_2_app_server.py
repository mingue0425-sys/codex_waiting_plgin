#!/usr/bin/env python3
from __future__ import annotations

"""Discover and exercise the installed App Server protocol conservatively.

All runtime mutations use disposable probe threads.  The probe never resumes,
steers, queues, injects into or interrupts a thread selected from the user's
thread list.  Active-turn experiments live in the separate interrupt and
concurrency probes.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.2"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.app_server_probe import AppServerClient, redact, response_error, write_trace

METHODS: Dict[str, Dict[str, Any]] = {
    "thread/start": {
        "request": "ThreadStartParams",
        "response": "ThreadStartResponse",
        "notifications": ["thread/started", "thread/status/changed"],
    },
    "thread/read": {
        "request": "ThreadReadParams",
        "response": "ThreadReadResponse",
        "notifications": [],
    },
    "thread/list": {
        "request": "ThreadListParams",
        "response": "ThreadListResponse",
        "notifications": [],
    },
    "thread/resume": {
        "request": "ThreadResumeParams",
        "response": "ThreadResumeResponse",
        "notifications": ["thread/status/changed"],
    },
    "thread/status (notification)": {
        "request": None,
        "response": "ThreadStatusChangedNotification",
        "notifications": ["thread/status/changed"],
    },
    "turn/start": {
        "request": "TurnStartParams",
        "response": "TurnStartResponse",
        "notifications": ["turn/started", "item/started", "turn/completed"],
    },
    "turn/interrupt": {
        "request": "TurnInterruptParams",
        "response": "TurnInterruptResponse",
        "notifications": ["turn/completed"],
    },
    "turn/steer": {
        "request": "TurnSteerParams",
        "response": "TurnSteerResponse",
        "notifications": ["item/started", "turn/completed"],
    },
    "turn/start.toolOutput": {
        "request": "TurnStartParams",
        "response": "TurnStartResponse",
        "field": "toolOutput",
        "notifications": [],
    },
    "thread/inject_items": {
        "request": "ThreadInjectItemsParams",
        "response": "ThreadInjectItemsResponse",
        "notifications": [],
    },
    "thread/backgroundTerminals/list": {
        "request": "ThreadBackgroundTerminalsListParams",
        "response": "ThreadBackgroundTerminalsListResponse",
        "notifications": [],
    },
    "thread/queue/list": {
        "request": "ThreadQueueListParams",
        "response": "ThreadQueueListResponse",
        "notifications": ["thread/queue/changed"],
    },
    "thread/queue/add": {
        "request": "ThreadQueueAddParams",
        "response": "ThreadQueueAddResponse",
        "notifications": ["thread/queue/changed"],
    },
}


def run(command: Sequence[str], timeout: float = 30.0) -> Dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(command),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=os.environ.copy(),
        )
        return {
            "argv": list(command),
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": list(command),
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": f"timeout after {timeout}s",
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    except OSError as exc:
        return {
            "argv": list(command),
            "returncode": 127,
            "stdout": "",
            "stderr": f"{type(exc).__name__}: {exc}",
            "duration_seconds": round(time.monotonic() - started, 6),
        }


def _find_schema(directory: Path) -> Optional[Path]:
    candidates = sorted(directory.rglob("codex_app_server_protocol.v2.schemas.json"))
    if candidates:
        return candidates[0]
    candidates = sorted(directory.rglob("*.schemas.json"))
    return candidates[0] if candidates else None


def _schema_record(value: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if value is None:
        return {"exists": False}
    return {
        "exists": True,
        "title": value.get("title"),
        "description": value.get("description"),
        "required": value.get("required", []),
        "properties": sorted((value.get("properties") or {}).keys()),
        "schema": redact(value),
    }


def _experimental_marker(value: Optional[Dict[str, Any]]) -> bool:
    if not value:
        return False
    text = json.dumps(value, ensure_ascii=False).lower()
    return "experimental" in text or "unstable" in text


def schema_inventory() -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v02-schema-") as temporary:
        temp_root = Path(temporary)
        stable_dir = temp_root / "stable"
        experimental_dir = temp_root / "experimental"
        stable_run = run(
            ["codex", "app-server", "generate-json-schema", "--out", str(stable_dir)],
            timeout=60,
        )
        experimental_run = run(
            [
                "codex",
                "app-server",
                "generate-json-schema",
                "--experimental",
                "--out",
                str(experimental_dir),
            ],
            timeout=60,
        )
        stable_path = _find_schema(stable_dir)
        experimental_path = _find_schema(experimental_dir)
        stable_defs: Dict[str, Any] = {}
        experimental_defs: Dict[str, Any] = {}
        if stable_path is not None:
            try:
                stable_defs = json.loads(stable_path.read_text(encoding="utf-8")).get("definitions", {})
            except (OSError, ValueError, AttributeError):
                stable_defs = {}
        if experimental_path is not None:
            try:
                experimental_defs = json.loads(experimental_path.read_text(encoding="utf-8")).get(
                    "definitions", {}
                )
            except (OSError, ValueError, AttributeError):
                experimental_defs = {}
        methods: Dict[str, Any] = {}
        for method, target in METHODS.items():
            request_name = target.get("request")
            response_name = target.get("response")
            request_schema = experimental_defs.get(request_name) if request_name else None
            response_schema = experimental_defs.get(response_name) if response_name else None
            field = target.get("field")
            field_exists = bool(
                field
                and isinstance(request_schema, dict)
                and field in (request_schema.get("properties") or {})
            )
            method_exists = (request_name is None or request_schema is not None) and response_schema is not None
            methods[method] = {
                "exists": method_exists and (field is None or field_exists),
                "experimental": bool(
                    (request_name and request_name not in stable_defs)
                    or (response_name and response_name not in stable_defs)
                    or _experimental_marker(request_schema)
                    or _experimental_marker(response_schema)
                ),
                "request_name": request_name,
                "response_name": response_name,
                "field": field,
                "field_exists": field_exists if field else None,
                "request_schema": _schema_record(request_schema),
                "response_schema": _schema_record(response_schema),
                "notifications": target.get("notifications", []),
                "runtime": "not_attempted",
            }
        schema_hash = None
        if experimental_path is not None:
            schema_hash = hashlib.sha256(experimental_path.read_bytes()).hexdigest()
        return {
            "stable_generation": stable_run,
            "experimental_generation": experimental_run,
            "stable_schema": str(stable_path) if stable_path else None,
            "experimental_schema": str(experimental_path) if experimental_path else None,
            "experimental_schema_sha256": schema_hash,
            "methods": methods,
            "status": "PASS"
            if experimental_path is not None and all(item["exists"] for item in methods.values())
            else "PARTIAL"
            if experimental_path is not None
            else "UNKNOWN",
        }


def _response_record(response: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if response is None:
        return {"error": "no response"}
    result = response.get("result")
    result_keys = sorted(result.keys()) if isinstance(result, dict) else []
    return {
        "error": response_error(response),
        "result_keys": result_keys,
        "response": redact(response),
    }


def _thread_id(response: Optional[Dict[str, Any]]) -> Optional[str]:
    if not response:
        return None
    result = response.get("result") or {}
    thread = result.get("thread") if isinstance(result, dict) else None
    return thread.get("id") if isinstance(thread, dict) else None


def _turn_id(response: Optional[Dict[str, Any]]) -> Optional[str]:
    if not response:
        return None
    result = response.get("result") or {}
    turn = result.get("turn") if isinstance(result, dict) else None
    return turn.get("id") if isinstance(turn, dict) else None


def _wait_turn_completed(client: AppServerClient, turn_id: str, timeout: float = 45.0) -> Optional[str]:
    notification = client.wait_for_notification(
        lambda item: item.get("method") == "turn/completed"
        and ((item.get("params") or {}).get("turn") or {}).get("id") == turn_id,
        timeout=timeout,
    )
    return (((notification or {}).get("params") or {}).get("turn") or {}).get("status")


def runtime_probe(live: bool) -> Dict[str, Any]:
    runtime: Dict[str, Any] = {
        "live_requested": live,
        "operations": [],
        "status": "UNKNOWN",
    }
    client = AppServerClient(cwd=ROOT)
    disposable_thread_id: Optional[str] = None
    turn_id: Optional[str] = None
    try:
        initialized = client.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "codex-snooze-v02-probe",
                    "title": "Codex Snooze v0.2 App Server Probe",
                    "version": "0.2.0",
                },
                "capabilities": {"experimentalApi": True, "requestAttestation": False},
            },
            timeout=20,
        )
        runtime["operations"].append({"method": "initialize", "params": {}, **_response_record(initialized)})
        if response_error(initialized):
            runtime["status"] = "UNKNOWN"
            return {**runtime, "trace": client.snapshot()}
        client.notify("initialized", {})

        listed = client.request("thread/list", {"limit": 20, "useStateDbOnly": True}, timeout=20)
        runtime["operations"].append(
            {"method": "thread/list", "params": {"limit": 20, "useStateDbOnly": True}, **_response_record(listed)}
        )
        threads = ((listed.get("result") or {}).get("data") or []) if not response_error(listed) else []
        runtime["listed_threads"] = [
            {
                "id": thread.get("id"),
                "status": (thread.get("status") or {}).get("type"),
                "path": thread.get("path"),
                "cwd": thread.get("cwd"),
            }
            for thread in threads
            if isinstance(thread, dict)
        ]

        # Read-only inspection of an unloaded thread is safe; no user thread is
        # resumed, steered, interrupted, queued or injected by this probe.
        selected = next(
            (
                thread
                for thread in threads
                if isinstance(thread, dict) and (thread.get("status") or {}).get("type") != "active"
            ),
            None,
        )
        if selected and selected.get("id"):
            selected_id = selected["id"]
            read_user = client.request(
                "thread/read", {"threadId": selected_id, "includeTurns": False}, timeout=20
            )
            runtime["operations"].append(
                {
                    "method": "thread/read",
                    "target": "listed_non_active_thread",
                    "params": {"threadId": selected_id, "includeTurns": False},
                    **_response_record(read_user),
                }
            )
        else:
            runtime["operations"].append(
                {
                    "method": "thread/read",
                    "target": "listed_non_active_thread",
                    "status": "UNKNOWN",
                    "reason": "no non-active listed thread was available",
                }
            )

        started = client.request("thread/start", {"cwd": str(ROOT), "ephemeral": False}, timeout=20)
        runtime["operations"].append(
            {"method": "thread/start", "params": {"cwd": str(ROOT), "ephemeral": False}, **_response_record(started)}
        )
        disposable_thread_id = _thread_id(started)
        if disposable_thread_id is None:
            runtime["status"] = "PARTIAL"
            return {**runtime, "trace": client.snapshot()}

        disposable_read = client.request(
            "thread/read", {"threadId": disposable_thread_id, "includeTurns": False}, timeout=20
        )
        runtime["operations"].append(
            {
                "method": "thread/read",
                "target": "disposable_thread",
                "params": {"threadId": disposable_thread_id, "includeTurns": False},
                **_response_record(disposable_read),
            }
        )
        background = client.request(
            "thread/backgroundTerminals/list", {"threadId": disposable_thread_id, "limit": 20}, timeout=20
        )
        runtime["operations"].append(
            {
                "method": "thread/backgroundTerminals/list",
                "params": {"threadId": disposable_thread_id, "limit": 20},
                **_response_record(background),
            }
        )
        queue_list = client.request(
            "thread/queue/list", {"threadId": disposable_thread_id, "limit": 20}, timeout=20
        )
        runtime["operations"].append(
            {
                "method": "thread/queue/list",
                "params": {"threadId": disposable_thread_id, "limit": 20},
                **_response_record(queue_list),
            }
        )
        resume_before_turn = client.request(
            "thread/resume", {"threadId": disposable_thread_id, "excludeTurns": True}, timeout=20
        )
        runtime["operations"].append(
            {
                "method": "thread/resume",
                "target": "disposable_thread_before_rollout",
                "params": {"threadId": disposable_thread_id, "excludeTurns": True},
                **_response_record(resume_before_turn),
            }
        )

        if live and not response_error(started):
            marker = f"SNOOZE_V02_APP_SERVER_{int(time.time())}"
            turn = client.request(
                "turn/start",
                {
                    "threadId": disposable_thread_id,
                    "input": [{"type": "text", "text": f"Do not use tools. Reply with exactly {marker}."}],
                },
                timeout=30,
            )
            turn_id = _turn_id(turn)
            runtime["operations"].append(
                {
                    "method": "turn/start",
                    "target": "disposable_thread",
                    "params": {"threadId": disposable_thread_id, "input": [{"type": "text", "text": marker}]},
                    **_response_record(turn),
                }
            )
            if turn_id:
                completed_status = _wait_turn_completed(client, turn_id, timeout=60)
                runtime["operations"].append(
                    {
                        "method": "turn/completed",
                        "turn_id": turn_id,
                        "status": completed_status or "UNKNOWN",
                    }
                )
                resume_after_turn = client.request(
                    "thread/resume", {"threadId": disposable_thread_id, "excludeTurns": True}, timeout=20
                )
                runtime["operations"].append(
                    {
                        "method": "thread/resume",
                        "target": "disposable_thread_after_rollout",
                        "params": {"threadId": disposable_thread_id, "excludeTurns": True},
                        **_response_record(resume_after_turn),
                    }
                )
        else:
            runtime["operations"].extend(
                [
                    {"method": "turn/start", "status": "UNKNOWN", "reason": "live probe not requested"},
                    {"method": "turn/interrupt", "status": "UNKNOWN", "reason": "tested separately"},
                    {"method": "turn/steer", "status": "UNKNOWN", "reason": "tested separately"},
                ]
            )
        runtime["notification_methods"] = [item.get("method") for item in client.notifications]
        runtime["status"] = "PASS" if all(
            operation.get("error") is None
            for operation in runtime["operations"]
            if operation.get("method") in {"initialize", "thread/list", "thread/start"}
        ) else "PARTIAL"
        return {**runtime, "trace": client.snapshot()}
    except (OSError, RuntimeError, TimeoutError, ValueError, KeyError) as exc:
        runtime["status"] = "UNKNOWN"
        runtime["error"] = f"{type(exc).__name__}: {exc}"
        return {**runtime, "trace": client.snapshot()}
    finally:
        if turn_id:
            try:
                client.request(
                    "turn/interrupt", {"threadId": disposable_thread_id, "turnId": turn_id}, timeout=10
                )
            except (OSError, RuntimeError, TimeoutError, ValueError):
                pass
        if disposable_thread_id:
            try:
                client.request("thread/delete", {"threadId": disposable_thread_id}, timeout=10)
            except (OSError, RuntimeError, TimeoutError, ValueError):
                pass
        client.close()


def build_payload(live: bool) -> Dict[str, Any]:
    schema = schema_inventory()
    runtime = runtime_probe(live)
    methods = schema.get("methods", {})
    for operation in runtime.get("operations", []):
        method = operation.get("method")
        if method in methods:
            methods[method]["runtime"] = "PASS" if operation.get("error") is None else "PARTIAL"
            methods[method].setdefault("runtime_observations", []).append(
                {key: value for key, value in operation.items() if key not in {"response", "params"}}
            )
    return {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "codex_version": run(["codex", "--version"])["stdout"].strip(),
        "platform": {"system": os.uname().sysname, "machine": os.uname().machine},
        "live_requested": live,
        "schema_inventory": schema,
        "runtime_probe": runtime,
        "methods": methods,
        "capabilities": {
            "app_server_initialize": "PASS"
            if any(
                operation.get("method") == "initialize" and operation.get("error") is None
                for operation in runtime.get("operations", [])
            )
            else "UNKNOWN",
            "thread_list": "PASS"
            if any(
                operation.get("method") == "thread/list" and operation.get("error") is None
                for operation in runtime.get("operations", [])
            )
            else "UNKNOWN",
            "thread_start_read_background_queue_list": "PASS"
            if all(
                any(operation.get("method") == name and operation.get("error") is None for operation in runtime.get("operations", []))
                for name in ("thread/start", "thread/read", "thread/backgroundTerminals/list", "thread/queue/list")
            )
            else "PARTIAL",
            "thread_resume_before_rollout": "PASS"
            if any(
                operation.get("method") == "thread/resume" and operation.get("error") is None
                for operation in runtime.get("operations", [])
            )
            else "UNKNOWN",
            "turn_start_interrupt_steer": "UNKNOWN",
            "busy_thread_delivery": "UNKNOWN",
        },
    }


def write_outputs(payload: Dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "app-server-probes.json", payload)
    lines = [
        "# v0.2 App Server probes",
        "",
        f"Generated: `{payload['generated_at']}`",
        f"Codex: `{payload['codex_version'].strip()}`",
        f"Live model calls requested: `{payload['live_requested']}`",
        "",
        "The runtime probe used only disposable threads for mutations. A listed",
        "user thread was inspected at most with read-only `thread/read`.",
        "",
        "| Method or field | Exists | Experimental | Runtime | Request | Response |",
        "|---|---|---|---|---|---|",
    ]
    for method, value in payload["methods"].items():
        lines.append(
            f"| `{method}` | {value['exists']} | {value['experimental']} | {value['runtime']} | "
            f"`{value.get('request_name') or 'notification'}` | `{value.get('response_name') or 'field'}` |"
        )
    lines.extend(["", "## Runtime operations", ""])
    for operation in payload["runtime_probe"].get("operations", []):
        lines.append(
            f"- `{operation.get('method')}` target=`{operation.get('target', '')}` "
            f"error=`{operation.get('error')}` status=`{operation.get('status', '')}`"
        )
    lines.extend(["", "## Capability summary", ""])
    for name, status in payload["capabilities"].items():
        lines.append(f"- `{name}`: **{status}**")
    lines.extend(
        [
            "",
            "Full request/response/notification trace and schema summaries are in",
            "`app-server-probes.json`. Active-turn interrupt, steer and delivery",
            "experiments are recorded by their dedicated v0.2 result files.",
            "",
        ]
    )
    (OUT / "app-server-probes.md").write_text("\n".join(lines), encoding="utf-8")


def main(argv: Optional[List[str]] = None) -> int:
    live = bool(argv and "--live" in argv)
    payload = build_payload(live)
    write_outputs(payload)
    print(json.dumps({"status": payload["runtime_probe"]["status"], "live": live}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
