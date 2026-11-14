from __future__ import annotations

import json
from pathlib import Path

from pilot.internal.atomic_file import atomic_write_private_text

FILENAME = ".patches.json"


def is_applied(bench_dir: Path, patch_name: str) -> bool:
    """Whether the bench already applied this patch."""
    return patch_name in _read(bench_dir)


def mark_applied(bench_dir: Path, patch_name: str) -> None:
    """Record that the bench applied this patch."""
    applied = _read(bench_dir)
    if patch_name in applied:
        return
    applied.add(patch_name)
    atomic_write_private_text(bench_dir / FILENAME, json.dumps({"applied": sorted(applied)}))


def _read(bench_dir: Path) -> set[str]:
    """The names of the patches the bench applied."""
    path = bench_dir / FILENAME
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return set()
    return set(data.get("applied", [])) if isinstance(data, dict) else set()
