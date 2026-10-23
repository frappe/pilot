from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.core.app.node_packages import NodePackages, get_app_packages
from pilot.exceptions import BenchError, CommandError
from pilot.managers.systemd_user import can_cap_memory, memory_capped, systemctl_env
from pilot.utils import run_command

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
        from pilot.core.build_memory import build_swap

        with build_swap():
            self._run_capped(argv, **kwargs)

    def _run_capped(self, argv: list[str], **kwargs) -> None:
        from pilot.core.build_memory import build_memory_limit_mb, build_swap_limit_mb, can_read_memory

        limit_mb = build_memory_limit_mb(self.bench.config.build.memory_limit_mb) if can_read_memory() else 0
        capped = bool(limit_mb) and can_cap_memory()
        command = argv[0]
        if capped:
            swap_mb = build_swap_limit_mb()
            argv = memory_capped(argv, limit_mb, swap_mb)
        else:
            logging.warning("Memory control unavailable here, so this build runs uncapped.")

        kwargs["env"] = self.get_build_env(kwargs.get("env") or {}, limit_mb)
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
                error.message.replace(repr("systemd-run"), repr(command), 1),
                returncode=error.returncode,
            ) from error

    @staticmethod
    def get_build_env(env: dict, limit_mb: int) -> dict:
        env = {**systemctl_env(), **env}
        node_options = env.get("NODE_OPTIONS", "")
        # V8 stops near 2GB whatever the cap; leave a quarter of the cap for native memory.
        if limit_mb and "--max-old-space-size" not in node_options:
            env["NODE_OPTIONS"] = f"{node_options} --max-old-space-size={limit_mb * 3 // 4}".strip()
        return env

    def build_assets(self) -> None:
        for app in self.bench.apps():
            NodePackages(app.path).ensure_installed()
        self.run_compiler(
            [*self.bench.frappe_call, "frappe", "build", "--force"],
            cwd=self.bench.sites_path,
            env=self.manager._build_env(),
            stream_output=True,
        )

    def build_assets_for_app(self, app: "App", force: bool = False) -> None:
        # Desk serves /assets/frappe/node_modules, and every server build runs Frappe's esbuild.
        if app.config.name == "frappe":
            NodePackages(app.path).ensure_installed()

        if not force and not app.has_source_changes and self.install_prebuilt_assets(app):
            return

        with app.record_lockfiles_on_failure():
            self.build_app_from_source(app)

    def install_prebuilt_assets(self, app: "App") -> bool:
        """Install the app's published assets and build what the archive lacks. False when none are published."""
        from pilot.core.app.prebuilt_assets import PrebuiltAssets

        prebuilt = PrebuiltAssets(app)
        if not prebuilt.install():
            return False
        app_public_dir = app.path / app.config.name / "public"
        self.setup_prebuilt_assets(
            app.config.name, app_public_dir, app_public_dir / "dist", prebuilt.asset_maps, prebuilt.has_page_islands
        )
        if app.has_page_islands and not prebuilt.has_page_islands:
            self.build_page_islands()
        if app.has_studio_apps and not prebuilt.has_studio_apps:
            self.build_studio_apps(app)
        return True

    def build_app_from_source(self, app: "App") -> None:
        # Frappe's build runs the app's build script, which can build `frontend`/`roster`, so
        # their node_modules must be installed before it.
        for packages in get_app_packages(app.path, self.frontend_dirs(app)):
            packages.ensure_installed()
        # Frappe's `after_app_build` hook builds the app's Studio apps with this editor.
        if app.has_studio_apps:
            self.studio_editor.ensure_installed()

        print(f"  Building assets for {app.config.name}...")
        sys.stdout.flush()
        self.run_compiler(
            [*self.bench.frappe_call, "frappe", "build", "--force", "--app", app.config.name],
            cwd=self.bench.sites_path,
            env=self.manager._build_env(),
            stream_output=True,
        )

        # Frappe's build already ran the app's own build script, which builds its frontends.
        if NodePackages(app.path).has_build_script:
            return
        for frontend_dir in self.frontend_dirs(app):
            print(f"  Building {frontend_dir.name} for {app.config.name}...")
            sys.stdout.flush()
            self.run_compiler(
                NodePackages(frontend_dir, app.path).get_script_command("build"),
                cwd=frontend_dir,
                env=self.manager._build_env(),
                stream_output=True,
            )

    @staticmethod
    def frontend_dirs(app: "App") -> list[Path]:
        """`frontend`, `roster` and each build dir that `[tool.bench.assets]` declares, such as
        Helpdesk's `desk`, that has a package.json."""
        from pilot.core.app.prebuilt_assets import asset_build_dirs

        names = dict.fromkeys(["frontend", "roster", *asset_build_dirs(app)])
        return [app.path / name for name in names if NodePackages(app.path / name).has_package_json]

    def setup_prebuilt_assets(
        self,
        app_name: str,
        app_public_dir: Path,
        dist_dir: Path,
        asset_maps: dict[str, dict[str, str]] | None = None,
        has_page_islands: bool = False,
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

            # Islands from the archive replace the bench's; otherwise the bench's stay for its rebuild.
            keeping = "" if has_page_islands else PAGE_ISLAND_URL
            for name, entries in asset_maps.items():
                self.merge_json(assets_dir / name, entries, replacing=f"/assets/{app_name}/", keeping=keeping)
        print(f"  Linked {app_link} -> {app_public_dir.resolve()}")

    def build_page_islands(self) -> None:
        """Build every app's page islands, as Frappe's `after_app_build` hook does. Frappe
        without them (version-16) has no script."""
        frappe_path = self.bench.apps_path / "frappe"
        script = frappe_path / "ui" / "vite" / "island" / "build-pages.js"
        if not script.exists():
            return
        NodePackages(script.parent / "toolchain").ensure_installed()
        print("  Building Frappe UI page islands...")
        sys.stdout.flush()
        self.run_compiler(["node", str(script), "--production"], cwd=frappe_path, stream_output=True)

    def build_studio_apps(self, app: "App") -> None:
        """Build the app's exported Studio apps into `public/app_builds`, as Frappe's
        `after_app_build` hook does. Frappe without a Studio editor (version-16) cannot."""
        if not self.studio_editor.has_package_json:
            return
        self.studio_editor.ensure_installed()
        print(f"  Building Studio apps for {app.config.name}...")
        sys.stdout.flush()
        build = f"from frappe.studio.build import build_standard_apps; build_standard_apps([{app.config.name!r}])"
        self.run_compiler(
            [str(self.bench.python), "-c", build],
            cwd=self.bench.sites_path,
            env=self.manager._build_env(),
            stream_output=True,
        )

    @property
    def studio_editor(self) -> NodePackages:
        return NodePackages(self.bench.apps_path / "frappe" / "studio" / "frontend")

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
