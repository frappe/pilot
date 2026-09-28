from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.utils import write_private_text

if TYPE_CHECKING:
    from pilot.core.worktree import Worktree

_MANIFESTS = ("assets.json", "assets-rtl.json")
# Shared with the main bench as links; `chromium` exists only where PDF rendering was set up.
_SHARED_ENTRIES = ("env", "chromium")


class WorktreeLayout:
    """The overlay bench: the worktree app beside links to main's other apps, env and config."""

    def __init__(self, worktree: "Worktree") -> None:
        self.worktree = worktree
        self.main = worktree.bench
        self.overlay = worktree.runtime_bench

    def sync(self) -> None:
        """Rebuild links and config from the main bench. Safe to repeat."""
        self.overlay.create_directories()
        self.link_main_apps()
        self.link_shared_entries()
        (self.overlay.sites_path / "apps.txt").write_text(self._main_text("apps.txt"))
        write_private_text(
            self.overlay.sites_path / "common_site_config.json",
            self._main_text("common_site_config.json") or "{}",
        )
        self.overlay.write_common_site_config()
        self.merge_asset_manifests()

    def link_main_apps(self) -> None:
        """Link every main app except the worktree app, and drop links to apps main no longer has."""
        for entry in self.overlay.apps_path.iterdir():
            if entry.is_symlink() and not entry.exists():
                entry.unlink()
        for app_dir in sorted(self.main.apps_path.iterdir()):
            if app_dir.is_dir() and app_dir.name != self.worktree.config.app:
                _link(self.overlay.apps_path / app_dir.name, app_dir)

    def link_shared_entries(self) -> None:
        for name in _SHARED_ENTRIES:
            if (self.main.path / name).exists():
                _link(self.overlay.path / name, self.main.path / name)

    def merge_asset_manifests(self) -> None:
        """Main's asset entries plus the overlay's own entries for the worktree app."""
        prefix = f"/assets/{self.worktree.config.app}/"
        for filename in _MANIFESTS:
            main_entries = _read_json(self.main.sites_path / "assets" / filename)
            path = self.overlay.sites_path / "assets" / filename
            own_entries = {
                key: value
                for key, value in _read_json(path).items()
                if isinstance(value, str) and value.startswith(prefix)
            }
            if main_entries or own_entries:
                path.write_text(json.dumps({**main_entries, **own_entries}, indent=4))

    def remove_links(self) -> None:
        """Unlink everything shared with main, so removing the overlay cannot reach into it."""
        entries = [*self.overlay.apps_path.glob("*"), *(self.overlay.path / name for name in _SHARED_ENTRIES)]
        for entry in entries:
            if entry.is_symlink():
                entry.unlink()

    def _main_text(self, filename: str) -> str:
        path = self.main.sites_path / filename
        return path.read_text() if path.exists() else ""


def _link(link: Path, target: Path) -> None:
    relative_target = os.path.relpath(target, link.parent)
    if link.is_symlink():
        if os.readlink(link) == relative_target:
            return
        link.unlink()
    elif link.exists():
        return
    link.symlink_to(relative_target)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}
