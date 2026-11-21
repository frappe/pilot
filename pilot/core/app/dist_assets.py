from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable, Iterable
from pathlib import Path

from pilot.internal.atomic_file import atomic_write_private_text, exchange_paths

RETAINED_ASSET_BUILDS = 2


class DistAssets:
    """Publish a complete dist tree with a bounded history of immutable files."""

    history_file = ".pilot-asset-builds.json"
    hashed_name = re.compile(r"(?:\.[\w-]{8}|-[A-Z0-9]{8})\.[a-z0-9]+(?:\.map)?$", re.ASCII)

    def __init__(self, path: Path) -> None:
        self.path = path

    @property
    def hashed_paths(self) -> set[str]:
        return {
            path.relative_to(self.path).as_posix()
            for path in self.path.rglob("*")
            if self.hashed_name.search(path.name) and path.is_file() and not path.is_symlink()
        }

    def get_builds(self, available: set[str]) -> list[set[str]]:
        history = self.path / self.history_file
        if not history.exists():
            return [available]
        builds = [set(build) for build in json.loads(history.read_text())]
        if not builds or not builds[0] <= available or not available <= set.union(*builds):
            return [available]
        return [build & available for build in builds]

    @property
    def retained_paths(self) -> set[str]:
        if not (self.path / self.history_file).exists():
            return set()
        available = self.hashed_paths
        builds = self.get_builds(available)
        return set.union(*builds[1:]) - builds[0] if len(builds) > 1 else set()

    def get_copy_ignore(self, extra: Iterable[str] = ()) -> Callable[[str, list[str]], set[str]]:
        """Exclude retained files when a build uses the current outputs as inputs."""
        excluded = self.retained_paths | {self.history_file, *extra}

        def ignore(folder: str, names: list[str]) -> set[str]:
            relative = Path(folder).relative_to(self.path)
            return {name for name in names if (relative / name).as_posix() in excluded}

        return ignore

    def publish(self, staged: Path, retain_generations: int) -> None:
        """Carry forward immutable files before exposing any new output."""
        target = DistAssets(staged)
        target.retain(self, retain_generations)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            exchange_paths(staged, self.path)
        else:
            staged.rename(self.path)

    def snapshot(self, target: Path) -> DistAssets:
        paths = self.hashed_paths
        for relative in paths:
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.path / relative, destination)
        atomic_write_private_text(
            target / self.history_file, json.dumps([sorted(build) for build in self.get_builds(paths)]) + "\n"
        )
        return DistAssets(target)

    def retain(self, source: "DistAssets", retain_generations: int) -> None:
        """Carry bounded history from the outgoing dist into this one."""
        paths = self.hashed_paths
        builds = [self.get_builds(paths)[0]]
        for previous in source.get_builds(source.hashed_paths):
            if previous not in builds:
                builds.append(previous)
        builds = builds[:retain_generations + 1]
        retained = set.union(*builds)
        for relative in retained - paths:
            target = self.path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            os.link(source.path / relative, target)
        for relative in paths - retained:
            (self.path / relative).unlink()
        atomic_write_private_text(
            self.path / self.history_file, json.dumps([sorted(build) for build in builds]) + "\n"
        )
