#!/usr/bin/env python3
from __future__ import annotations

"""Compare the existing supervisor with experimental process/spawn."""

import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.4"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.app_server_process import AppServerProcess
from snooze_controller.native_backend import NativeAppServerBackend, NativeBackendError
from snooze_core.models import utc_now
from snooze_core.store import JobStore
from tools.app_server_probe import redact, write_trace


def custom_run(root: Path) -> Dict[str, Any]:
    store = JobStore(root / "custom-store")
    command = (
        f"{sys.executable} -c "
        + repr("import sys,time; print('CUSTOM_STDOUT'); print('CUSTOM_STDERR', file=sys.stderr); time.sleep(.4)")
    )
    spec = __import__("snooze_core.models", fromlist=["JobSpec"]).JobSpec.create(command=command, cwd=root, shell="/bin/zsh")
    store.create(spec)
    from snooze_core.cli import _spawn_supervisor

    process = _spawn_supervisor(store, spec.job_id, None)
    returncode = process.wait(timeout=30)
    result = store.read_result(spec.job_id)
    return {
        "supervisor_returncode": returncode,
        "execution_state": (result or {}).get("execution_state"),
        "exit_code": (result or {}).get("exit_code"),
        "log_preview": (result or {}).get("log_preview", ""),
        "result_hash_present": bool((result or {}).get("result_sha256")),
        "handoff_suitable": True,
    }


def native_run(root: Path) -> Dict[str, Any]:
    process = AppServerProcess(cwd=ROOT, experimental_api=True)
    value: Dict[str, Any] = {}
    try:
        process.start(timeout=30)
        backend = NativeAppServerBackend(process)
        handle = backend.start(
            [sys.executable, "-c", "import sys,time; print('NATIVE_STDOUT'); print('NATIVE_STDERR', file=sys.stderr); time.sleep(.4)"],
            cwd=root,
            timeout_ms=10000,
            stream_output=True,
        )
        result = backend.wait(handle, timeout=30)
        value = {
            "result": result,
            "exit_integrity": result.get("exit_code") == 0 and "NATIVE_STDOUT" in result.get("stdout", ""),
            "logging_observed": "NATIVE_STDOUT" in result.get("stdout", "") and "NATIVE_STDERR" in result.get("stderr", ""),
            "sandbox": "FAIL",
            "approval": "UNKNOWN",
            "handoff_suitable": False,
            "reason": "installed process/spawn schema describes host execution without a Codex sandbox",
        }
    except (OSError, RuntimeError, ValueError, NativeBackendError) as exc:
        value = {"error": f"{type(exc).__name__}: {exc}", "exit_integrity": False, "logging_observed": False, "sandbox": "UNKNOWN", "approval": "UNKNOWN", "handoff_suitable": False}
    finally:
        process.stop()
    return value


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v04-backend-") as temporary:
        root = Path(temporary)
        custom = custom_run(root)
        native = native_run(root)
        value = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "custom_supervisor": custom,
            "native_process_backend": native,
            "custom_vs_native_exit_integrity": "PASS" if custom.get("exit_code") == native.get("result", {}).get("exit_code") == 0 else "FAIL",
            "custom_vs_native_logging": "PASS" if "CUSTOM_STDOUT" in custom.get("log_preview", "") and native.get("logging_observed") else "PARTIAL",
            "native_sandbox": native.get("sandbox", "UNKNOWN"),
            "native_approval": native.get("approval", "UNKNOWN"),
            "native_backend_mode": "EXPERIMENTAL_ONLY",
            "status": "PARTIAL" if custom.get("exit_code") == 0 and native.get("exit_integrity") and native.get("sandbox") == "FAIL" else "UNKNOWN",
            "exactly_once": "NOT_CLAIMED",
        }
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "backend-differential.json", redact(value))
    (OUT / "backend-differential.md").write_text(
        "# v0.4 custom supervisor and native backend\n\n"
        f"Status: **{value['status']}**\n\n"
        f"Exit integrity: **{value['custom_vs_native_exit_integrity']}**\n\n"
        f"Native sandbox: **{value['native_sandbox']}**\n\n"
        "The native process/spawn API is retained as experimental because the installed schema explicitly describes host execution without a Codex sandbox.\n",
        encoding="utf-8",
    )
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
