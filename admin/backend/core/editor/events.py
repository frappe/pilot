from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator
from pathlib import Path

_POLL_SECONDS = 1.5
_KEEPALIVE_SECONDS = 25
_SCAN_CAP = 30000
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv"}


def file_events(root: Path) -> Iterator[str]:
    """Emit a change ping whenever the workspace file tree changes on disk.

    ponytail: O(tree) mtime poll every 1.5s; swap for a filesystem-watch dep if
    latency or large-tree cost ever matters.
    """
    yield from _poll(lambda: _tree_signature(root))


def git_events(git) -> Iterator[str]:
    """Emit a change ping whenever git status or HEAD changes."""
    yield from _poll(lambda: _git_signature(git))


def _poll(signature) -> Iterator[str]:
    last = signature()
    idle = 0.0
    while True:
        time.sleep(_POLL_SECONDS)
        current = signature()
        if current != last:
            last = current
            idle = 0.0
            yield f"data: {json.dumps({'type': 'change'})}\n\n"
            continue
        idle += _POLL_SECONDS
        if idle >= _KEEPALIVE_SECONDS:
            idle = 0.0
            yield ": keepalive\n\n"


def _tree_signature(root: Path) -> int:
    digest = 0
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            try:
                digest ^= hash((os.path.join(dirpath, name), int(os.path.getmtime(os.path.join(dirpath, name)))))
            except OSError:
                continue
            count += 1
            if count >= _SCAN_CAP:
                return digest
    return digest


def _git_signature(git) -> str:
    return git._text("status", "--porcelain") + "|" + git._text("rev-parse", "HEAD")
