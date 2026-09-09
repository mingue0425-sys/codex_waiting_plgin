#!/usr/bin/env python3
from __future__ import annotations

"""Exercise the explicit handoff contract through zsh and bash."""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.4"
LAUNCHER = ROOT / "scripts" / "codex-snooze"


def _run(shell: str) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"codex-snooze-v04-{Path(shell).name}-") as temporary:
        root = Path(temporary)
        unicode_name = "unicode ☃ dir"
        script = (
            "from pathlib import Path; "
            f"Path({unicode_name!r}).mkdir(exist_ok=True); "
            "Path('python marker.txt').write_text('PYTHON_OK\\n', encoding='utf-8')"
        )
        shell_command = (
            "set -eu; "
            "value='quoted value'; "
            "mkdir -p 'unicode ☃ dir'; "
            "printf 'alpha\\n' | tr 'a-z' 'A-Z' > 'unicode ☃ dir/pipeline output.txt' && "
            "printf '%s\\n' \"$value\" > 'unicode ☃ dir/quoted output.txt' && "
            f"{shlex.join([sys.executable, '-c', script])} && "
            "printf '%s\\n' \"$(cat 'python marker.txt')\" >> 'unicode ☃ dir/quoted output.txt'"
        )
        command = [
            sys.executable,
            str(LAUNCHER),
            "--store",
            str(root / "store"),
            "handoff",
            "--threshold",
            "10",
            "--cwd",
            str(root),
            "--shell",
            shell,
            "--command",
            shell_command,
        ]
        short = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
        short_value: Dict[str, Any] = {"returncode": short.returncode, "stderr": short.stderr[-1000:]}
        if short.returncode == 0:
            try:
                short_output = json.loads(short.stdout)
                store = root / "store" / "jobs" / short_output["job_id"]
                result = json.loads((store / "result.json").read_text(encoding="utf-8"))
                short_value.update(
                    {
                        "state": short_output.get("state"),
                        "exit_code": short_output.get("exit_code"),
                        "result_state": result.get("execution_state"),
                        "pipeline": (root / unicode_name / "pipeline output.txt").read_text(encoding="utf-8").strip()
                        if (root / unicode_name / "pipeline output.txt").exists()
                        else None,
                        "quoted": (root / unicode_name / "quoted output.txt").read_text(encoding="utf-8").splitlines()
                        if (root / unicode_name / "quoted output.txt").exists()
                        else None,
                        "python_marker": (root / "python marker.txt").read_text(encoding="utf-8").strip()
                        if (root / "python marker.txt").exists()
                        else None,
                    }
                )
            except (OSError, ValueError, KeyError) as exc:
                short_value["error"] = f"result parse: {type(exc).__name__}: {exc}"
        short_pass = (
            short_value.get("returncode") == 0
            and short_value.get("state") == "COMPLETED"
            and short_value.get("exit_code") == 0
            and short_value.get("result_state") == "COMPLETED"
            and short_value.get("pipeline") == "ALPHA"
            and short_value.get("quoted") == ["quoted value", "PYTHON_OK"]
            and short_value.get("python_marker") == "PYTHON_OK"
        )

        long_command = f"{shlex.join([sys.executable, '-c', 'import time; time.sleep(1.0); print(\"LONG_OK\")'])}"
        detached_command = [
            sys.executable,
            str(LAUNCHER),
            "--store",
            str(root / "detached-store"),
            "handoff",
            "--threshold",
            "0.2",
            "--cwd",
            str(root),
            "--shell",
            shell,
            "--command",
            long_command,
        ]
        detached = subprocess.run(detached_command, cwd=ROOT, capture_output=True, text=True, timeout=30, check=False)
        detached_value: Dict[str, Any] = {"returncode": detached.returncode, "stderr": detached.stderr[-1000:]}
        detached_pass = False
        if detached.returncode == 0:
            try:
                marker = json.loads(detached.stdout)
                job_dir = root / "detached-store" / "jobs" / marker["job_id"]
                marker_file = job_dir / "handoff.json"
                for _ in range(100):
                    if (job_dir / "result.json").exists():
                        break
                    time.sleep(0.05)
                result = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
                detached_value.update(
                    {
                        "state": marker.get("state"),
                        "marker_persisted": marker_file.exists(),
                        "execution_state": result.get("execution_state"),
                        "exit_code": result.get("exit_code"),
                        "log_contains_long_ok": "LONG_OK" in result.get("log_preview", ""),
                    }
                )
                detached_pass = (
                    marker.get("state") == "DETACHED"
                    and marker_file.exists()
                    and result.get("execution_state") == "COMPLETED"
                    and result.get("exit_code") == 0
                    and detached_value["log_contains_long_ok"]
                )
            except (OSError, ValueError, KeyError) as exc:
                detached_value["error"] = f"detached parse: {type(exc).__name__}: {exc}"
        return {
            "shell": shell,
            "short_transparency": "PASS" if short_pass else "FAIL",
            "short": short_value,
            "detached_handoff": "PASS" if detached_pass else "FAIL",
            "detached": detached_value,
        }


def main() -> int:
    shells = [candidate for candidate in ("/bin/zsh", "/bin/bash") if Path(candidate).is_file() and os.access(candidate, os.X_OK)]
    results = [_run(shell) for shell in shells]
    value = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "results": results,
        "zsh_handoff": next((item["detached_handoff"] for item in results if item["shell"] == "/bin/zsh"), "UNKNOWN"),
        "bash_handoff": next((item["detached_handoff"] for item in results if item["shell"] == "/bin/bash"), "UNKNOWN"),
        "status": "PASS" if results and all(item["short_transparency"] == "PASS" and item["detached_handoff"] == "PASS" for item in results) else "FAIL" if results else "UNKNOWN",
        "semantics": "threshold_seconds is a foreground handoff wait, not a command timeout or kill deadline",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "shell-handoff.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# v0.4 shell handoff", "", f"Status: **{value['status']}**", "", "| Shell | Short transparency | Detached handoff |", "|---|---|---|"]
    lines.extend(f"| `{item['shell']}` | `{item['short_transparency']}` | `{item['detached_handoff']}` |" for item in results)
    lines.extend(["", value["semantics"], ""])
    (OUT / "shell-handoff.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0 if value["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
