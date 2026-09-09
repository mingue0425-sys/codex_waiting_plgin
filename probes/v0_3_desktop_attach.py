#!/usr/bin/env python3
from __future__ import annotations

"""Bounded, observation-only Desktop attach probe.

The probe inspects process ancestry and standard file-descriptor *types* and
reads public Codex help.  It never opens another process's descriptors,
injects code, reads memory, extracts credentials or mutates a Desktop thread.
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "v0.3"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.app_server_probe import redact, utc_now, write_trace


def run(argv: List[str], timeout: float = 10.0) -> Dict[str, Any]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=os.environ.copy(),
        )
        return {
            "argv": argv,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "argv": argv,
            "returncode": 124 if isinstance(exc, subprocess.TimeoutExpired) else 127,
            "stdout": "",
            "stderr": str(exc),
            "duration_seconds": round(time.monotonic() - started, 6),
        }


def _processes() -> List[Dict[str, Any]]:
    result = run(["ps", "-axo", "pid=,ppid=,comm=,args="], timeout=10)
    processes: List[Dict[str, Any]] = []
    for line in result.get("stdout", "").splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        comm = parts[2]
        args = parts[3] if len(parts) > 3 else ""
        lowered = f"{comm} {args}".lower()
        argv0 = args.split(None, 1)[0] if args else comm
        executable = Path(argv0).name.lower()
        classification = "other"
        if executable in {"chatgpt", "codex"} and "app-server" not in lowered:
            classification = "desktop"
        elif "app-server" in lowered and executable == "codex":
            classification = "app_server"
        elif executable == "codex":
            classification = "codex"
        processes.append(
            {
                "pid": pid,
                "ppid": ppid,
                "comm": comm,
                "classification": classification,
                # The full command can contain user prompts or paths.  Keep
                # only stable executable-shaped evidence in the artifact.
                "argv_head": Path(argv0).name if args else comm,
                "transport": (
                    re.search(r"--listen\s+([^\s]+)", args).group(1)
                    if re.search(r"--listen\s+([^\s]+)", args)
                    else None
                ),
            }
        )
    return processes


def _tree(processes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_parent: Dict[int, List[Dict[str, Any]]] = {}
    for item in processes:
        by_parent.setdefault(int(item["ppid"]), []).append(item)
    roots = [item for item in processes if item["classification"] == "desktop"]
    included: Dict[int, Dict[str, Any]] = {}
    queue = [int(item["pid"]) for item in roots]
    while queue:
        pid = queue.pop(0)
        if pid in included:
            continue
        item = next((candidate for candidate in processes if int(candidate["pid"]) == pid), None)
        if item is None:
            continue
        included[pid] = item
        queue.extend(int(child["pid"]) for child in by_parent.get(pid, []))
    return list(included.values())


def _descriptor_types(pid: int) -> Dict[str, Any]:
    """Observe fd type labels from lsof without opening or duplicating fds."""
    result = run(["lsof", "-nP", "-a", "-p", str(pid), "-d", "0,1,2"], timeout=5)
    counts: Dict[str, int] = {}
    for line in result.get("stdout", "").splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 5:
            kind = fields[4]
            counts[kind] = counts.get(kind, 0) + 1
    return {"pid": pid, "returncode": result["returncode"], "types": counts}


def _public_help() -> Dict[str, Any]:
    commands = {
        "app_server_help": ["codex", "app-server", "--help"],
        "app_server_daemon_help": ["codex", "app-server", "daemon", "--help"],
        "app_server_version": ["codex", "--version"],
    }
    values: Dict[str, Any] = {}
    terms = ("attach", "reconnect", "socket", "pipe", "stdio", "endpoint", "connect")
    for name, argv in commands.items():
        result = run(argv, timeout=15)
        text = (result.get("stdout", "") + "\n" + result.get("stderr", "")).lower()
        values[name] = {
            "argv": argv,
            "returncode": result["returncode"],
            "mentions": {term: term in text for term in terms},
            "line_count": len(text.splitlines()),
        }
    return values


def probe() -> Dict[str, Any]:
    processes = _processes()
    desktop = [item for item in processes if item["classification"] == "desktop"]
    tree = _tree(processes)
    app_servers = [item for item in tree if item["classification"] == "app_server"]
    codex = [item for item in tree if item["classification"] in {"codex", "app_server"}]
    help_values = _public_help()
    descriptors = [_descriptor_types(int(item["pid"])) for item in desktop]
    externally_addressable = any(
        item.get("transport") and not str(item["transport"]).startswith("stdio://")
        for item in app_servers
    )
    documented_attach = any(item["mentions"].get("attach") or item["mentions"].get("reconnect") for item in help_values.values())
    if desktop and app_servers and externally_addressable and documented_attach:
        status = "PASS"
        meaning = "A public help surface appears to advertise an external attach/reconnect endpoint; adapter work still requires a separate protocol proof."
    elif desktop and app_servers:
        status = "FAIL"
        meaning = "Desktop owns an App Server child, but the observed transport is stdio and no supported external attach/reconnect endpoint is exposed."
    else:
        status = "UNKNOWN"
        meaning = "No Desktop process was observable in this session."
    return redact(
        {
            "schema_version": 1,
            "generated_at": utc_now(),
            "probe": "observation_only_desktop_attach",
            "status": status,
            "meaning": meaning,
            "desktop_process_count": len(desktop),
            "desktop_processes": desktop,
            "codex_descendants": codex,
            "app_server_children": app_servers,
            "process_tree": tree,
            "standard_fd_types": descriptors + [_descriptor_types(int(item["pid"])) for item in app_servers],
            "public_help": help_values,
            "transport_observed": "descriptor_type_only",
            "credential_extraction": "NOT_ATTEMPTED",
            "memory_or_debugger_injection": "NOT_ATTEMPTED",
            "fd_stealing": "NOT_ATTEMPTED",
            "thread_mutation": "NOT_ATTEMPTED",
        }
    )


def markdown(value: Dict[str, Any]) -> str:
    lines = [
        "# v0.3 Desktop attach probe",
        "",
        f"Generated: `{value['generated_at']}`",
        f"Status: **{value['status']}**",
        "",
        value["meaning"],
        "",
        "The probe used public process/help observation only. It did not inject,",
        "open or duplicate another process's descriptors, read memory or mutate a",
        "thread. Standard descriptor output is reduced to type counts.",
        "",
        f"Desktop processes observed: `{value['desktop_process_count']}`",
        f"Codex descendants in the Desktop tree: `{len(value['codex_descendants'])}`",
        f"App Server children in the Desktop tree: `{len(value['app_server_children'])}`",
        "",
        "| Public surface | Return code | Attach terms observed |",
        "|---|---:|---|",
    ]
    for name, item in value["public_help"].items():
        terms = ", ".join(key for key, present in item["mentions"].items() if present) or "none"
        lines.append(f"| `{name}` | {item['returncode']} | {terms} |")
    lines.extend(
        [
            "",
            "A `FAIL` result closes the Desktop attach investigation for v0.3 and",
            "selects the Snooze-owned App Server path. It does not imply that durable",
            "thread history, a live runtime and a Desktop UI are the same object.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    value = probe()
    OUT.mkdir(parents=True, exist_ok=True)
    write_trace(OUT / "desktop-attach.json", value)
    (OUT / "desktop-attach.md").write_text(markdown(value), encoding="utf-8")
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
