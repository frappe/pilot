from __future__ import annotations

import subprocess
from pathlib import Path


def clone_app(repo: str, target: str, target_type: str, clone_dir: Path) -> None:
    if target_type == "commit":
        _clone_commit(repo, target, clone_dir)
    else:
        _clone_ref(repo, target, clone_dir)


def _clone_ref(repo: str, ref: str, clone_dir: Path) -> None:
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", ref, repo, str(clone_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not clone {repo}@{ref}: {result.stderr.strip()}")


def _clone_commit(repo: str, commit: str, clone_dir: Path) -> None:
    clone_dir.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", str(clone_dir)])
    _run(["git", "-C", str(clone_dir), "fetch", "--depth", "1", repo, commit])
    _run(["git", "-C", str(clone_dir), "checkout", "FETCH_HEAD"])


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
