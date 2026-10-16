import logging
import os
import shutil
import tempfile
from pathlib import Path

from pilot.core.bench.artifacts import BenchArtifacts
from pilot.exceptions import CommandError
from pilot.internal.atomic_file import exclusive_file_lock
from pilot.managers.node_dependencies import NodeDependencies


class NodeDependencyCache:
    """Reuse completed portable installs without sharing writable dependency files."""

    def __init__(self, bench) -> None:
        self.root = bench.path.parent / ".pilot" / "node-cache"

    @staticmethod
    def get_key(path: Path) -> str | None:
        if not all((path / name).is_file() for name in ("package.json", "yarn.lock")):
            return None
        if not NodeDependencies.is_portable(path):
            return None
        return NodeDependencies.get_key(path)

    def capture(self, path: Path) -> None:
        key = self.get_key(path)
        if key is None or (path / "node_modules").is_symlink():
            return
        if not NodeDependencies.has_matching_install(path, key):
            return
        try:
            self.publish(path, key)
        except (OSError, CommandError) as error:
            logging.warning("Skipping Node dependency cache publication: %s", error)

    def publish(self, path: Path, key: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.root / f"{key}.guard"):
            target = self.root / key
            if NodeDependencies.has_matching_install(target, key):
                return
            with tempfile.TemporaryDirectory(prefix="install-", dir=self.root) as temporary:
                staging = Path(temporary)
                BenchArtifacts.copy_directory(path / "node_modules", staging / "node_modules")
                if not self.has_internal_links(staging / "node_modules", path / "node_modules"):
                    return
                BenchArtifacts.relocate_links(path.resolve(), target.resolve(), root=staging)
                if self.get_key(path) != key or not NodeDependencies.has_matching_install(path, key):
                    return
                if not NodeDependencies.has_matching_install(staging, key):
                    return
                if target.exists():
                    shutil.rmtree(target)
                staging.rename(target)

    def restore(self, path: Path) -> bool:
        key = self.get_key(path)
        if key is None or not self.root.is_dir():
            return False
        target = self.root / key
        if not NodeDependencies.has_matching_install(target, key):
            return False
        # Completed entries are immutable, so concurrent forks can read them together.
        modules = path / "node_modules"
        if modules.exists() or modules.is_symlink():
            if modules.is_symlink():
                modules.unlink()
            else:
                shutil.rmtree(modules)
        BenchArtifacts.copy_directory(target / "node_modules", modules)
        BenchArtifacts.relocate_links(target.resolve(), path.resolve(), root=modules)
        return True

    @staticmethod
    def has_internal_links(copied: Path, original: Path) -> bool:
        for directory, directories, files in os.walk(copied):
            for name in [*directories, *files]:
                path = Path(directory) / name
                if not path.is_symlink():
                    continue
                link = Path(os.readlink(path))
                base = original / path.relative_to(copied).parent
                try:
                    internal = (base / link).resolve(strict=True).is_relative_to(original.resolve())
                except (OSError, RuntimeError):
                    return False
                if not internal:
                    return False
        return True
