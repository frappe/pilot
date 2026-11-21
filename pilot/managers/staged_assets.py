from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.core.app.dist_assets import RETAINED_ASSET_BUILDS, DistAssets
from pilot.internal.atomic_file import atomic_write_private_text

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class StagedAssets:
    """A private Frappe Node build tree backed by the real app sources."""

    manifests = ("assets.json", "assets-rtl.json")
    esbuild_dirs = ("js", "css", "css-rtl")

    def __init__(self, bench: Bench, root: Path, esbuild_apps: set[str]) -> None:
        self.bench = bench
        self.root = root
        self.esbuild_apps = esbuild_apps
        self.live = bench.sites_path / "assets"
        self.assets = root / "sites" / "assets"
        self.dists = {
            app.config.name: DistAssets(app.path / app.config.name / "public" / "dist")
            for app in bench.apps()
        }

    def prepare(self) -> None:
        self.assets.mkdir(parents=True)
        (self.root / "apps").symlink_to(self.bench.apps_path)
        for name in ("apps.txt", "common_site_config.json"):
            (self.assets.parent / name).symlink_to(self.bench.sites_path / name)
        for app, dist in self.dists.items():
            self.prepare_dist(app, dist)
        esbuild_prefixes = tuple(prefix for app in self.esbuild_apps for prefix in self.get_esbuild_prefixes(app))
        for name in self.manifests:
            live = self.live / name
            entries = json.loads(live.read_text()) if live.exists() else {}
            entries = {key: url for key, url in entries.items() if not url.startswith(esbuild_prefixes)}
            (self.assets / name).write_text(json.dumps(entries))

    @classmethod
    def get_esbuild_prefixes(cls, app: str) -> tuple[str, ...]:
        return tuple(f"/assets/{app}/dist/{folder}/" for folder in cls.esbuild_dirs)

    def prepare_dist(self, app: str, dist: DistAssets) -> None:
        if not dist.path.is_dir():
            return
        excluded = self.esbuild_dirs if app in self.esbuild_apps else ()
        shutil.copytree(
            dist.path, self.assets / app / "dist", symlinks=True, ignore=dist.get_copy_ignore(excluded)
        )

    def publish(self) -> None:
        for app, dist in self.dists.items():
            staged = self.assets / app / "dist"
            if staged.is_dir():
                dist.publish(staged, RETAINED_ASSET_BUILDS)
        for name in self.manifests:
            atomic_write_private_text(self.live / name, (self.assets / name).read_text())
