from __future__ import annotations

"""Shared bounded and scrubbed helpers for v0.5 runtime artifacts."""

from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable


def scrub_paths(value: Any, root: Path, project_root: Path) -> Any:
    variants = {str(root), str(root.resolve())}
    if str(root).startswith("/") and not str(root).startswith("/private/"):
        variants.add("/private" + str(root))
    if isinstance(value, dict):
        return {str(key): scrub_paths(item, root, project_root) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_paths(item, root, project_root) for item in value]
    if isinstance(value, str):
        for candidate in sorted(variants, key=len, reverse=True):
            value = value.replace(candidate, "<v05-fixture-root>")
        return value.replace(str(project_root), "<project-root>")
    return value


def compact_item(item: Any) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {"type": type(item).__name__}
    value: Dict[str, Any] = {}
    for key in ("id", "type", "itemId", "command", "processId", "status", "exitCode", "cwd", "source"):
        if key in item:
            value[key] = item[key]
    if "aggregatedOutput" in item:
        output = item.get("aggregatedOutput")
        value["aggregated_output_bytes"] = len(output.encode("utf-8")) if isinstance(output, str) else None
    return value


def compact_params(method: Any, params: Any) -> Dict[str, Any]:
    method = str(method)
    params = params if isinstance(params, dict) else {}
    value: Dict[str, Any] = {}
    if isinstance(params.get("item"), dict):
        value["item"] = compact_item(params["item"])
    if isinstance(params.get("turn"), dict):
        turn = params["turn"]
        value["turn"] = {key: turn.get(key) for key in ("id", "status", "startedAt", "completedAt", "durationMs") if key in turn}
    if "status" in params:
        value["status"] = params.get("status")
    for key in ("threadId", "turnId", "itemId", "processId", "processHandle", "stream", "exitCode"):
        if key in params:
            value[key] = params.get(key)
    if method == "item/commandExecution/requestApproval":
        for key in ("command", "cwd", "reason", "approvalId", "kind"):
            if key in params:
                value[key] = params.get(key)
    return value


def compact_snapshot(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    events = snapshot.get("events", []) if isinstance(snapshot, dict) else []
    relevant: list[Dict[str, Any]] = []
    methods: Counter[str] = Counter()
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        method = event.get("method")
        if method is not None:
            methods[str(method)] += 1
        kind = event.get("kind")
        if kind == "lifecycle":
            relevant.append({"sequence": event.get("sequence"), "kind": kind, "state": event.get("state"), "reason": event.get("reason")})
        elif kind == "request":
            relevant.append({"sequence": event.get("sequence"), "kind": kind, "request_id": event.get("request_id"), "method": method})
        elif kind == "response" and event.get("error") is not None:
            relevant.append({"sequence": event.get("sequence"), "kind": kind, "request_id": event.get("request_id"), "error": event.get("error")})
        elif kind == "server_request":
            relevant.append({"sequence": event.get("sequence"), "kind": kind, "request_id": event.get("request_id"), "method": method})
        elif kind == "notification" and method in {
            "thread/started",
            "thread/status/changed",
            "turn/started",
            "turn/completed",
            "item/started",
            "item/completed",
            "item/commandExecution/outputDelta",
            "item/commandExecution/requestApproval",
            "process/outputDelta",
            "process/exited",
        }:
            relevant.append(
                {
                    "sequence": event.get("sequence"),
                    "kind": kind,
                    "method": method,
                    "params": compact_params(method, event.get("params")),
                }
            )
    return {
        "state": snapshot.get("state"),
        "experimental_api": snapshot.get("experimental_api"),
        "restart_count": snapshot.get("restart_count"),
        "event_count": len(events) if isinstance(events, list) else 0,
        "notification_count": len(snapshot.get("notifications", [])) if isinstance(snapshot, dict) and isinstance(snapshot.get("notifications", []), list) else 0,
        "notification_method_counts": dict(sorted(methods.items())),
        "relevant_events": relevant[-200:],
        "server_request_methods": [
            item.get("method") for item in snapshot.get("server_requests", []) if isinstance(item, dict)
        ][:100],
        "stderr_line_count": len(snapshot.get("stderr_tail", [])) if isinstance(snapshot.get("stderr_tail", []), list) else 0,
    }


__all__ = ["compact_snapshot", "scrub_paths"]
