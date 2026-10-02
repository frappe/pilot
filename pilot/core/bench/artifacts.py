from __future__ import annotations

import os
import sys
from pathlib import Path

from pilot.utils import run_command


class BenchArtifacts:
    """Copy prepared dependencies and assets, relocating links to their new owner."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    @staticmethod
    def paths(bench) -> list[str]:
        paths = ["sites/assets"]
        for app in bench.apps():
            prefix = f"apps/{app.config.name}"
            paths.extend(
                f"{prefix}/{suffix}"
                for suffix in (
                    "node_modules",
                    "frontend/node_modules",
                    "roster/node_modules",
                    f"{app.module_name}/public",
                )
            )
        return [path for path in paths if (bench.path / path).is_dir()]

    def capture(self, bench) -> list[str]:
        existing = self.paths(bench)
        for path in existing:
            self.copy_directory(bench.path / path, self.root / path)
        self.relocate_links(bench.path.resolve(), self.root)
        return existing

    def restore(self, bench, paths: list[str]) -> None:
        for path in paths:
            self.copy_directory(self.root / path, bench.path / path)
        self.relocate_links(self.root, bench.path.resolve())

    @staticmethod
    def copy_directory(source: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        argv = ["cp", "-a"]
        if sys.platform == "linux":
            argv.append("--reflink=auto")
        run_command([*argv, str(source) + "/.", str(destination)])

    @staticmethod
    def relocate_links(source: Path, destination: Path) -> None:
        directories = [destination]
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
                        directories.append(entry.path)
