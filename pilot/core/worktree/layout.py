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
        self.link_assets()
        (self.overlay.sites_path / "apps.txt").write_text(self._main_text("apps.txt"))
        write_private_text(
            self.overlay.sites_path / "common_site_config.json",
            self._main_text("common_site_config.json") or "{}",
        )
        self.overlay.write_common_site_config()
        self.merge_asset_manifests()

    def link_main_apps(self) -> None:
        """Link every main app except the worktree app, and drop links to apps main no longer has."""
        _drop_dangling_links(self.overlay.apps_path)
        for app_dir in sorted(self.main.apps_path.iterdir()):
            if app_dir.is_dir() and app_dir.name != self.worktree.config.app:
                _link(self.overlay.apps_path / app_dir.name, app_dir)

    def link_shared_entries(self) -> None:
        for name in _SHARED_ENTRIES:
            if (self.main.path / name).exists():
                _link(self.overlay.path / name, self.main.path / name)

    def link_assets(self) -> None:
        """The `sites/assets` links Frappe's `make_asset_dirs` would make, without its side effect.

        Frappe also relinks `<app>/public/node_modules` through `sites/assets/<app>`, which for a
        linked app is a write into main's checkout. Only the worktree app gets that link here."""
        assets = self.overlay.sites_path / "assets"
        _drop_dangling_links(assets)
        for app_dir in sorted(self.overlay.apps_path.iterdir()):
            public = app_dir / app_dir.name / "public"
            if public.is_dir():
                _link(assets / app_dir.name, public)
        node_modules = self.worktree.app_path / "node_modules"
        if node_modules.is_dir():
            _link(self.worktree.app_path / self.worktree.config.app / "public" / "node_modules", node_modules)

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


def _drop_dangling_links(directory: Path) -> None:
    for entry in directory.iterdir():
        if entry.is_symlink() and not entry.exists():
            entry.unlink()


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}
