"""A disposable Pilot installation root for subprocess CLI benchmarks.

The CLI resolves its benches directory from the package location, so running
`bin/pilot` from a copy makes `pilot ls`, `pilot new`, and friends operate on
the synthetic fixture instead of the developer's real benches.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from pilot.utils import cli_root


def make_install_root(destination: Path) -> Path:
    """Copy the Pilot package, admin backend, and CLI into `destination`."""
    source = cli_root()
    shutil.rmtree(destination, ignore_errors=True)
    destination.mkdir(parents=True, exist_ok=True)

    shutil.copytree(source / "pilot", destination / "pilot", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(
        source / "admin",
        destination / "admin",
        ignore=shutil.ignore_patterns("__pycache__", "frontend", "static"),
    )
    shutil.copytree(source / "bin", destination / "bin")
    for name in ("pyproject.toml", "VERSION"):
        path = source / name
        if path.exists():
            shutil.copy2(path, destination / name)
    return destination


__all__ = ["make_install_root"]
