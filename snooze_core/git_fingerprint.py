from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional


def _run_git(cwd: Path, args: list[str], timeout: float = 5.0) -> Optional[bytes]:
    try:
        completed = subprocess.run(
            ["git", "--no-optional-locks", *args],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def _digest(value: Optional[bytes]) -> Optional[str]:
    if value is None:
        return None
    return hashlib.sha256(value).hexdigest()


def fingerprint(cwd: Path) -> Dict[str, Any]:
    root_bytes = _run_git(cwd, ["rev-parse", "--show-toplevel"])
    if root_bytes is None:
        return {
            "kind": "NOT_A_GIT_REPO",
            "root": None,
            "head": None,
            "branch": None,
            "status_porcelain_v2_hash": None,
            "untracked_files_manifest_hash": None,
            "index_diff_hash": None,
            "working_tree_diff_hash": None,
        }

    root = Path(root_bytes.decode("utf-8", "replace").strip())
    head_bytes = _run_git(root, ["rev-parse", "HEAD"])
    branch_bytes = _run_git(root, ["symbolic-ref", "--quiet", "--short", "HEAD"])
    if branch_bytes is None:
        branch = "DETACHED"
    else:
        branch = branch_bytes.decode("utf-8", "replace").strip()
    status = _run_git(root, ["status", "--porcelain=v2", "--untracked-files=all"])
    untracked_manifest = _run_git(root, ["ls-files", "--others", "--exclude-standard", "-z"])
    index_diff = _run_git(root, ["diff", "--cached", "--binary"])
    worktree_diff = _run_git(root, ["diff", "--binary"])
    return {
        "kind": "GIT",
        "root": str(root),
        "head": head_bytes.decode("utf-8", "replace").strip()
        if head_bytes is not None
        else None,
        "branch": branch,
        "status_porcelain_v2_hash": _digest(status),
        "untracked_files_manifest_hash": _digest(untracked_manifest),
        "index_diff_hash": _digest(index_diff),
        "working_tree_diff_hash": _digest(worktree_diff),
    }


def changed(start: Optional[Dict[str, Any]], end: Optional[Dict[str, Any]]) -> bool:
    if start is None or end is None:
        return False
    if start.get("kind") != end.get("kind"):
        return True
    if start.get("kind") != "GIT":
        return False
    keys = (
        "root",
        "head",
        "branch",
        "status_porcelain_v2_hash",
        "untracked_files_manifest_hash",
        "index_diff_hash",
        "working_tree_diff_hash",
    )
    return any(start.get(key) != end.get(key) for key in keys)
