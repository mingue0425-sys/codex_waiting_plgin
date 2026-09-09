#!/usr/bin/env python3
from __future__ import annotations

"""Live benign fixture for the experimental App Server process backend."""

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.3"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from snooze_controller.app_server_process import AppServerProcess
from snooze_controller.native_backend import NativeAppServerBackend, NativeBackendError
from tools.app_server_probe import redact, utc_now, write_trace


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="codex-snooze-v03-native-") as temporary:
        root = Path(temporary)
        marker = root / "native-marker.json"
        process = AppServerProcess(cwd=ROOT, experimental_api=True)
        value = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "backend": "NativeAppServerBackend",
            "experimental_api": True,
            "status": "UNKNOWN",
        }
        try:
            init = process.start(timeout=30)
            backend = NativeAppServerBackend(process)
            handle = backend.start(
                [
                    sys.executable,
                    "-c",
                    (
                        "import json,time; from pathlib import Path; "
                        f"time.sleep(2); Path({str(marker)!r}).write_text(json.dumps({{'ok': True}}))"
                    ),
                ],
                cwd=root,
                timeout_ms=15000,
            )
            started = time.monotonic()
            result = backend.wait(handle, timeout=30)
            value.update(
                {
                    "status": "PASS" if result.get("exit_code") == 0 and marker.exists() else "FAIL",
                    "initialize_result_keys": sorted((init.get("result") or {}).keys()),
                    "result": result,
                    "marker_written": marker.exists(),
                    "wall_seconds": round(time.monotonic() - started, 3),
                    "process_notifications": [
                        item.get("method")
                        for item in process.notifications
                        if item.get("method", "").startswith("process/")
                    ],
                    "approval_policy": "not_auto_allowed",
                    "sandbox_parity": "UNKNOWN",
                }
            )
        except (OSError, RuntimeError, NativeBackendError) as exc:
            value.update({"status": "UNKNOWN", "error": f"{type(exc).__name__}: {exc}"})
        finally:
            process.stop()
        value["app_server_state_after_stop"] = process.state.value
    OUT.mkdir(parents=True, exist_ok=True)
    value = redact(value)
    write_trace(OUT / "native-backend.json", value)
    (OUT / "native-backend.md").write_text(
        "# v0.3 native App Server backend\n\n"
        f"Status: **{value['status']}**\n\n"
        "The experimental `process/spawn` API was exercised with a Python fixture. "
        "This proves the API's process lifecycle for the fixture only; sandbox and "
        "approval parity remain separate gates.\n",
        encoding="utf-8",
    )
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
