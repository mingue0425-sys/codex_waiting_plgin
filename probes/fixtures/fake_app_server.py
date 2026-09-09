#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time


def send(value: dict, *, split: bool = False) -> None:
    payload = (json.dumps(value, separators=(",", ":")) + "\n").encode()
    if split and len(payload) > 4:
        sys.stdout.buffer.write(payload[:4])
        sys.stdout.buffer.flush()
        time.sleep(0.01)
        sys.stdout.buffer.write(payload[4:])
    else:
        sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--crash-after-initialize", action="store_true")
    parser.add_argument("--drop-turn-response", action="store_true")
    parser.add_argument("--server-request", action="store_true")
    args = parser.parse_args()
    for raw in sys.stdin.buffer:
        try:
            message = json.loads(raw)
        except ValueError:
            continue
        if "method" not in message:
            continue
        method = message["method"]
        request_id = message.get("id")
        if method == "initialize":
            send({"id": request_id, "result": {"userAgent": "fake-v03", "platformOs": "test"}}, split=True)
            if args.crash_after_initialize:
                os._exit(17)
        elif method == "thread/start":
            send({"id": request_id, "result": {"thread": {"id": "fake-thread"}, "model": "fake"}})
            send({"method": "thread/status/changed", "params": {"threadId": "fake-thread", "status": {"type": "idle"}}})
        elif method == "thread/resume":
            send({"id": request_id, "result": {"thread": {"id": message.get("params", {}).get("threadId", "fake-thread")}}})
        elif method == "turn/start":
            if args.drop_turn_response:
                continue
            if args.server_request:
                send({"id": 77, "method": "item/commandExecution/requestApproval", "params": {"command": ["benign"]}})
            send({"id": request_id, "result": {"turn": {"id": "fake-turn"}}})
            send({"method": "turn/completed", "params": {"threadId": "fake-thread", "turn": {"id": "fake-turn", "status": "completed"}}})
        elif method == "thread/backgroundTerminals/list":
            send({"id": request_id, "result": {"data": [], "nextCursor": None}})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
