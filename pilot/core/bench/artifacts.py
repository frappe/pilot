from __future__ import annotations

import os
import sys
from pathlib import Path

from pilot.exceptions import CommandError
from pilot.utils import run_command


class BenchArtifacts:
    """Copy prepared dependencies and assets, relocating links to their new owner."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def restore(self, bench, paths: list[str]) -> None:
        for path in paths:
            self.copy_directory(self.root / path, bench.path / path)
            self.relocate_links(self.root, bench.path.resolve(), root=bench.path / path)

    @staticmethod
    def copy_directory(source: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        argv = ["cp", "-a"]
        if sys.platform == "linux":
            argv.append("--reflink=auto")
        elif sys.platform == "darwin":
            try:
                run_command([*argv, "-c", str(source) + "/.", str(destination)])
                return
            except CommandError:
                pass  # clonefile is unavailable across volumes or on non-APFS storage.
        run_command([*argv, str(source) + "/.", str(destination)])

    @staticmethod
    def relocate_links(source: Path, destination: Path, *, root: Path | None = None) -> None:
        directories = [root or destination]
        while directories:
            with os.scandir(directories.pop()) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        target = Path(os.readlink(entry.path))
                        if target.is_absolute() and target.is_relative_to(source):
                            path = Path(entry.path)
                            path.unlink()
                            path.symlink_to(destination / target.relative_to(source))
                    elif entry.is_dir(follow_symlinks=False):
                        directories.append(Path(entry.path))
