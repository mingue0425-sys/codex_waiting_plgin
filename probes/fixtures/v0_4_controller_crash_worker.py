#!/usr/bin/env python3
from __future__ import annotations

"""Worker for the controller-crash handoff E2E.

The process deliberately exits with os._exit after persisting the handoff
checkpoint. The parent process is responsible for recovery and cleanup.
"""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.agent import AgentController
from snooze_controller.app_server_process import AppServerProcess
from snooze_controller.handoff import DynamicHandoffTool, HandoffController
from snooze_controller.thread_registry import ThreadRegistry
from snooze_core.store import JobStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args()
    store = JobStore(args.store)
    registry = ThreadRegistry(args.registry)
    process = AppServerProcess(experimental_api=True)
    tool = DynamicHandoffTool(
        store,
        cwd=args.workspace,
        process=process,
        sandbox_policy={"type": "workspaceWrite", "writableRoots": [str(args.workspace.resolve())]},
    )
    process.request_handler = tool
    controller = AgentController(registry, cwd=args.workspace, process=process)
    handoff = HandoffController(controller, store, registry, poll_interval=0.05)
    process.start(timeout=30)
    controller.create_thread(
        cwd=args.workspace,
        sandbox="workspace-write",
        approval_policy="never",
        developer_instructions=(
            "When explicitly instructed to run this long job, use the named dynamic tool "
            "codex_snooze_handoff exactly once. After DETACHED, stop the turn."
        ),
        dynamic_tools=[DynamicHandoffTool.spec()],
        timeout=30,
    )
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
        args.token,
        "--exit-code",
        "0",
    ]
    turn = controller.start_turn(
        "Use codex_snooze_handoff exactly once with this exact argv vector: "
        + json.dumps(command)
        + ". Use threshold_seconds=0.5. Do not use the terminal tool or simulate output; stop after DETACHED.",
        timeout=60,
        cwd=args.workspace,
        approval_policy="never",
    )
    observation = handoff.wait_for_handoff(str(turn["id"]), timeout=120)
    if observation.marker is None or controller.thread_id is None:
        raise RuntimeError("handoff checkpoint was not created")
    checkpoint = {
        "thread_id": controller.thread_id,
        "turn_id": turn["id"],
        "marker": observation.marker,
        "item_id": observation.item_id,
        "turn": observation.turn,
        "app_server_pid": process.pid,
    }
    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.checkpoint.with_suffix(".tmp")
    temporary.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, args.checkpoint)
    with args.checkpoint.open("rb") as handle:
        os.fsync(handle.fileno())
    os._exit(73)


if __name__ == "__main__":
    raise SystemExit(main())
