from __future__ import annotations

import os
import typing
from pathlib import Path

from pilot.core.app.validator.base import IgnoredPaths
from pilot.exceptions import AppValidationError

if typing.TYPE_CHECKING:
    from pilot.core.app import App

SKIPPED_DIRS = {".git", "node_modules"}


class SymlinkCheck:
    """Rejects symlinks that do not resolve inside the app."""

    def run(self, app: "App") -> None:
        links = self.get_invalid_symlinks(app.path, IgnoredPaths(app))
        if not links:
            return
        raise AppValidationError(
            f"'{app.config.name}' contains symlinks that do not resolve inside the app:\n"
            + "\n".join(links)
            + "\nRemove them from the repository and commit the real files instead."
        )

    @staticmethod
    def _rejection_reason(path: Path, root: Path) -> str | None:
        """None for a link that resolves inside root, which is allowed."""
        if not path.exists():
            return "broken"
        if not path.resolve().is_relative_to(root):
            return "points outside the app"
        return None

    @classmethod
    def get_invalid_symlinks(cls, root: Path, ignored: IgnoredPaths | None = None) -> list[str]:
        """Symlinks under root that cannot survive packaging, described relative
        to it. Symlinks are matched before directories, so a symlinked directory
        is inspected rather than followed."""
        root = root.resolve()
        found = []
        pending = [root]
        while pending:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    relpath = path.relative_to(root)
                    if ignored and ignored.matches(relpath.as_posix()):
                        continue
                    if entry.is_symlink():
                        if reason := cls._rejection_reason(path, root):
                            found.append(f"  {relpath} -> {os.readlink(path)} ({reason})")
                    elif entry.is_dir() and entry.name not in SKIPPED_DIRS:
                        pending.append(path)
        return sorted(found)
