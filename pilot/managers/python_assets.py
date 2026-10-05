from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError, CommandError
from pilot.managers.systemd_user import can_cap_memory, memory_capped, systemctl_env
from pilot.utils import get_yarn_bin, run_command

if TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.managers.environment import PythonEnvManager

_BUNDLE_RE = re.compile(r"^(.+)\.bundle\.[A-Z0-9]{8}\.(js|css)$")


class PythonAssetBuilder:
    def __init__(self, manager: "PythonEnvManager") -> None:
        self.manager = manager
        self.bench = manager.bench

    def run_compiler(self, argv: list[str], **kwargs) -> None:
        """Run a compiler capped at a share of host memory, so a runaway build
        fails instead of exhausting the machine. Uncapped where the host cannot cap."""
        from pilot.core.build_memory import build_memory_limit_mb, build_swap_limit_mb, can_read_memory

        limit_mb = build_memory_limit_mb(self.bench.config.build.memory_limit_mb) if can_read_memory() else 0
        capped = bool(limit_mb) and can_cap_memory()
        if capped:
            swap_mb = build_swap_limit_mb()
            argv = memory_capped(argv, limit_mb, swap_mb)
        else:
            logging.warning("Memory control unavailable here, so this build runs uncapped.")

        env = {**systemctl_env(), **(kwargs.get("env") or {})}
        node_options = env.get("NODE_OPTIONS", "")
        # V8 stops near 2GB whatever the cap; leave a quarter of the cap for native memory.
        if limit_mb and "--max-old-space-size" not in node_options:
            env["NODE_OPTIONS"] = f"{node_options} --max-old-space-size={limit_mb * 3 // 4}".strip()
        kwargs["env"] = env
        try:
            run_command(argv, **kwargs)
        except CommandError as error:
            # The kernel kills the scope, so the runner only sees a signal.
            if capped and error.returncode < 0:
                raise BenchError(
                    f"Build ran out of memory: it may use {limit_mb}MB of RAM and {swap_mb}MB of swap "
                    "on this machine. Add swap, free memory, or set memory_limit_mb under [build] "
                    "in bench.toml, then retry."
                ) from error
            raise CommandError(
                error.message.replace(repr("systemd-run"), repr(argv[0]), 1),
                returncode=error.returncode,
            ) from error

    def build_assets(self) -> None:
        from pilot.core.bench.build_artifacts import BuildArtifacts

        artifacts = BuildArtifacts(self.bench)
        for app in self.bench.apps():
            if (app.path / "package.json").exists():
                self.ensure_yarn_install(app.path)
            self.ensure_frontend_dependencies(app)
        key = artifacts.get_key()
        self.run_compiler(
            [*self.bench.frappe_call, "frappe", "build", "--force"],
            cwd=self.bench.sites_path,
            env=self.manager._build_env(),
            stream_output=True,
        )
        if artifacts.get_key() == key:
            artifacts.capture(key)

    def build_assets_for_app(self, app: "App", force: bool = False) -> None:
        app_public_dir = app.path / app.config.name / "public"
        dist_dir = app_public_dir / "dist"

        # Desk serves /assets/frappe/node_modules, and every server build runs Frappe's esbuild.
        if app.config.name == "frappe" and (app.path / "package.json").exists():
            self.ensure_yarn_install(app.path)

        if not force and not app.has_source_changes:
            from pilot.core.app.prebuilt_assets import PrebuiltAssets

            prebuilt = PrebuiltAssets(app)
            if prebuilt.install():
                self.setup_prebuilt_assets(app.config.name, app_public_dir, dist_dir, prebuilt.asset_maps)
                if app.has_page_islands:
                    self.build_page_islands()
                return

        if (app.path / "package.json").exists():
            self.ensure_yarn_install(app.path)

        self.ensure_frontend_dependencies(app)

        print(f"  Building assets for {app.config.name}...")
        sys.stdout.flush()
        self.run_compiler(
            [*self.bench.frappe_call, "frappe", "build", "--force", "--app", app.config.name],
            cwd=self.bench.sites_path,
            env=self.manager._build_env(),
            stream_output=True,
        )

        for frontend_dir in ["frontend", "roster"]:
            if (app.path / frontend_dir / "package.json").exists():
                print(f"  Building {frontend_dir} for {app.config.name}...")
                sys.stdout.flush()
                self.run_compiler(
                    [get_yarn_bin(), "build"],
                    cwd=app.path / frontend_dir,
                    stream_output=True,
                )

    def ensure_frontend_dependencies(self, app: "App") -> None:
        """frappe's own `bench build` shells into `frontend`/`roster`, so node_modules must
        be synced there before that step runs, not just in the standalone build loop after it."""
        for frontend_dir in ["frontend", "roster"]:
            if (app.path / frontend_dir / "package.json").exists():
                print(f"  Installing JS dependencies for {frontend_dir} of {app.config.name}...")
                sys.stdout.flush()
                self.ensure_yarn_install(app.path / frontend_dir)

    def ensure_yarn_install(self, path: Path) -> None:
        """Install when dependency inputs or the local toolchain changed."""
        from pilot.managers.node_dependencies import NodeDependencies

        key = NodeDependencies.get_key(path)
        if NodeDependencies.has_matching_install(path, key):
            return
        app_name = path.name
        print(f"  Installing JS dependencies for {app_name}...")
        sys.stdout.flush()
        try:
            run_command(
                [get_yarn_bin(), "install", "--frozen-lockfile"],
                cwd=path,
                stream_output=True,
            )
        except Exception:
            print(f"  Frozen lockfile install failed for {app_name}; retrying without modifying yarn.lock...")
            sys.stdout.flush()
            run_command(
                [get_yarn_bin(), "install", "--pure-lockfile"],
                cwd=path,
                stream_output=True,
            )
        integrity = path / "node_modules" / ".yarn-integrity"
        if integrity.is_file():
            (integrity.parent / ".pilot-install-key").write_text(key)

    def setup_prebuilt_assets(
        self,
        app_name: str,
        app_public_dir: Path,
        dist_dir: Path,
        asset_maps: dict[str, dict[str, str]] | None = None,
    ) -> None:
        assets_dir = self.bench.sites_path / "assets"
        assets_dir.mkdir(exist_ok=True)

        app_link = assets_dir / app_name
        if app_link.is_symlink():
            app_link.unlink()
        elif app_link.is_dir():
            shutil.rmtree(str(app_link))
        app_link.symlink_to(app_public_dir.resolve())

        # As `bench build` links it: <app>/node_modules is served at /assets/<app>/node_modules.
        node_modules = app_public_dir.parent.parent / "node_modules"
        node_modules_link = app_public_dir / "node_modules"
        if node_modules.is_dir() and not node_modules_link.exists() and not node_modules_link.is_symlink():
            node_modules_link.symlink_to(node_modules.resolve())

        if asset_maps is None:
            self.write_assets_json(app_name, dist_dir, assets_dir)
        else:
            from pilot.core.app.prebuilt_assets import PAGE_ISLAND_URL

            for name, entries in asset_maps.items():
                self.merge_json(
                    assets_dir / name, entries, replacing=f"/assets/{app_name}/", keeping=PAGE_ISLAND_URL
                )
        print(f"  Linked {app_link} -> {app_public_dir.resolve()}")

    def build_page_islands(self) -> None:
        """Build every app's page islands, as Frappe's `after_app_build` hook does. Frappe
        without them (version-16) has no script."""
        frappe_path = self.bench.apps_path / "frappe"
        script = frappe_path / "ui" / "vite" / "island" / "build-pages.js"
        if not script.exists():
            return
        self.ensure_yarn_install(script.parent / "toolchain")
        print("  Building Frappe UI page islands...")
        sys.stdout.flush()
        self.run_compiler(["node", str(script), "--production"], cwd=frappe_path, stream_output=True)

    def write_assets_json(self, app_name: str, dist_dir: Path, assets_dir: Path) -> None:
        assets = {
            **self._bundle_manifest(app_name, dist_dir, "js", "js"),
            **self._bundle_manifest(app_name, dist_dir, "css", "css"),
        }
        rtl_assets = self._bundle_manifest(
            app_name,
            dist_dir,
            "css-rtl",
            "css",
            key_prefix="rtl_",
        )

        self.merge_json(assets_dir / "assets.json", assets)
        if rtl_assets:
            self.merge_json(assets_dir / "assets-rtl.json", rtl_assets)

    @staticmethod
    def _bundle_manifest(
        app_name: str,
        dist_dir: Path,
        directory: str,
        extension: str,
        *,
        key_prefix: str = "",
    ) -> dict[str, str]:
        bundle_dir = dist_dir / directory
        if not bundle_dir.is_dir():
            return {}

        entries = {}
        for path in sorted(bundle_dir.iterdir()):
            match = _BUNDLE_RE.match(path.name)
            if match and match.group(2) == extension:
                key = f"{key_prefix}{match.group(1)}.bundle.{extension}"
                entries[key] = f"/assets/{app_name}/dist/{directory}/{path.name}"
        return entries

    @staticmethod
    def merge_json(path: Path, new_entries: dict, replacing: str = "", keeping: str = "") -> None:
        """Merge under the lock: several apps' builds write this file at once. Entries under
        `replacing` are dropped first, except those under `keeping`."""
        from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked

        with exclusive_file_lock(path):
            existing: dict = {}
            if path.exists():
                with contextlib.suppress(json.JSONDecodeError):
                    existing = json.loads(path.read_text())
            if replacing:
                existing = {
                    key: url
                    for key, url in existing.items()
                    if not str(url).startswith(replacing) or (keeping and str(url).startswith(keeping))
                }
            existing.update(new_entries)
            replace_private_text_locked(path, json.dumps(existing, indent="\t", sort_keys=True) + "\n")
