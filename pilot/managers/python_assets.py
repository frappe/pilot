from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.core.app.node_packages import NodePackages, get_app_packages
from pilot.exceptions import BenchError, CommandError
from pilot.internal.atomic_file import atomic_write_private_text, exchange_paths, exclusive_file_lock
from pilot.internal.command import run_command
from pilot.managers.systemd_user import can_cap_memory, memory_capped, systemctl_env

if TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.core.bench import Bench
    from pilot.managers.environment import PythonEnvManager


PAGE_ISLAND_PATH = Path("dist/page-island")
ISLAND_PATHS = (PAGE_ISLAND_PATH, Path("dist/island"))
ASSET_MANIFEST_NAMES = ("assets.json", "assets-rtl.json")


def previous_bundles_root(bench: Bench) -> Path:
    return bench.sites_path.parent / ".previous-bundles"


@contextlib.contextmanager
def asset_build_lock(bench: Bench) -> Iterator[None]:
    # A per-app compiler can also mutate Frappe's assets through its build hook.
    root = previous_bundles_root(bench)
    root.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(root / "build"):
        yield


def discard_previous_bundles(bench: Bench, name: str) -> None:
    """Discard a removed app's fallback files while holding the bench asset lock."""
    root = previous_bundles_root(bench)
    snapshot = root / "assets" / name
    if snapshot.exists():
        shutil.rmtree(snapshot)
    (root / f".{name}.restored-islands.json").unlink(missing_ok=True)


def read_json_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


class PythonAssetBuilder:
    bundle_re = re.compile(r"^(.+)\.bundle\.[A-Z0-9]{8}\.(js|css)$")

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
        """Run a build command within the memory limit."""
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
        """The build environment with the Node.js memory limit."""
        env = {**systemctl_env(), **env}
        node_options = env.get("NODE_OPTIONS", "")
        # V8 stops near 2GB whatever the cap; leave a quarter of the cap for native memory.
        if limit_mb and "--max-old-space-size" not in node_options:
            env["NODE_OPTIONS"] = f"{node_options} --max-old-space-size={limit_mb * 3 // 4}".strip()
        return env

    def build_assets(self) -> None:
        """Build the assets of every app."""
        with asset_build_lock(self.bench), contextlib.ExitStack() as stack:
            apps = self.bench.apps()
            self.prepare_asset_fallback()
            self.repair_local_asset_maps()
            for app in apps:
                stack.enter_context(self.keeping_previous_bundles(app.module_name, app.path))
            for app in apps:
                for packages in get_app_packages(app.path, self.frontend_dirs(app)):
                    packages.ensure_installed()
            self.run_compiler(
                [*self.bench.frappe_call, "frappe", "build", "--force"],
                cwd=self.bench.sites_path,
                env=self.manager._build_env(),
                stream_output=True,
            )

    def build_assets_for_app(self, app: "App", force: bool = False, skip_frontend: bool = False) -> None:
        """Build one app's assets, from cache, release or source."""
        with asset_build_lock(self.bench):
            self.prepare_asset_fallback()
            self.repair_local_asset_maps()
            with self.keeping_previous_bundles(app.module_name, app.path):
                # Desk serves /assets/frappe/node_modules, and every server build runs Frappe's esbuild.
                if app.module_name == "frappe" and not skip_frontend:
                    NodePackages(app.path).ensure_installed()

                # A linked checkout is built from its own source, never from a release.
                if self._has_usable_prebuilt(app, force) and self.install_prebuilt_assets(
                    app, skip_frontend=skip_frontend
                ):
                    return

                with self.keeping_frappe_page_islands(app), app.record_lockfiles_on_failure():
                    self.build_app_from_source(app)

    def prepare_asset_fallback(self) -> None:
        """Load nginx's fallback locations before making the first destructive build."""
        from pilot.managers.nginx import NginxManager

        if self.bench.config.production.enabled is True:
            NginxManager(self.bench).prepare_asset_fallback()

    def repair_local_asset_maps(self) -> None:
        """Ensure Frappe's compiler can read the manifests it merges into."""
        for name in ASSET_MANIFEST_NAMES:
            path = self.bench.sites_path / "assets" / name
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            except ValueError:
                existing = None
            entries = (
                {key: url for key, url in existing.items() if isinstance(url, str)}
                if isinstance(existing, dict)
                else {}
            )
            if existing != entries:
                logging.warning("Rebuilding invalid entries in %s.", path)
                atomic_write_private_text(path, json.dumps(entries, indent="\t") + "\n")

    @property
    def page_island_script(self) -> Path:
        return self.bench.apps_path / "frappe" / "ui" / "vite" / "island" / "build-pages.js"

    @contextlib.contextmanager
    def keeping_frappe_page_islands(self, app: "App") -> Iterator[None]:
        """A single-app build can run the framework hook that rebuilds every page island."""
        if app.module_name != "frappe" and self.page_island_script.is_file() is True:
            with self.keeping_previous_bundles("frappe", self.bench.apps_path / "frappe", islands_only=True):
                yield
        else:
            yield

    @contextlib.contextmanager
    def keeping_previous_bundles(
        self, name: str, app_path: Path, *, islands_only: bool = False
    ) -> Iterator[None]:
        """Publish the outgoing build at nginx's fallback root, then restore missing files for dev."""
        public = (app_path / name / "public").resolve()
        root = previous_bundles_root(self.bench)
        app_snapshot = root / "assets" / name
        scope = PAGE_ISLAND_PATH if islands_only else Path()
        snapshot = app_snapshot / scope
        restored_index = root / f".{name}.restored-islands.json"
        restored = read_json_object(restored_index)
        outgoing = self.outgoing_bundles(name, public, app_snapshot, restored, islands_only=islands_only)
        if not outgoing and not snapshot.exists():
            yield
            return
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            staged = Path(temporary) / "snapshot"
            staged.mkdir()
            for relative in outgoing:
                source = public / relative
                if source.resolve() != source:
                    continue
                if not source.is_file():
                    source = app_snapshot / relative  # Recover a manifest entry after an interrupted build.
                if source.is_file():
                    target = staged / relative.relative_to(scope)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)

            if snapshot.exists():
                exchange_paths(staged, snapshot)
            else:
                staged.rename(snapshot)
            try:
                yield
            finally:
                remaining = self.prune_restored_islands(public, scope, restored)
                remaining.update(self.restore_missing_bundles(public, app_snapshot, scope))
                if restored != remaining:
                    atomic_write_private_text(restored_index, json.dumps(remaining))

    def prune_restored_islands(self, public: Path, scope: Path, restored: dict) -> dict:
        """Remove older compatibility copies that the compiler left in place."""
        remaining = {}
        for filename, stamp in restored.items():
            relative = Path(filename)
            if not relative.is_relative_to(scope):
                remaining[filename] = stamp
                continue
            path = public / relative
            if (
                self.is_in_island_output(relative)
                and path.resolve() == path
                and stamp is not None
                and self.island_file_stamp(path) == stamp
            ):
                path.unlink()
        return remaining

    def outgoing_bundles(
        self, name: str, public: Path, snapshot: Path, restored: dict, *, islands_only: bool
    ) -> set[Path]:
        prefix = f"/assets/{name}/"
        files = set()
        for manifest_name in ASSET_MANIFEST_NAMES:
            for url in read_json_object(self.bench.sites_path / "assets" / manifest_name).values():
                if not isinstance(url, str) or not url.startswith(f"{prefix}dist/") or "\0" in url:
                    continue
                relative = Path(url.removeprefix(prefix))
                if ".." not in relative.parts and (
                    not islands_only or relative.is_relative_to(PAGE_ISLAND_PATH)
                ):
                    files.add(relative)
        for directory in (PAGE_ISLAND_PATH,) if islands_only else ISLAND_PATHS:
            files.update(self.outgoing_islands(public, snapshot, directory, files, restored))
        return files

    def outgoing_islands(
        self, public: Path, snapshot: Path, directory: Path, entries: set[Path], restored: dict
    ) -> set[Path]:
        # When the manifest still points at a recovered entry, its unlisted chunks and media
        # are part of the current build too. Recover them together, including after a killed build.
        recovering = any(
            entry.is_relative_to(directory)
            and (snapshot / entry).is_file()
            and (
                not (public / entry).is_file()
                or restored.get(str(entry)) == self.island_file_stamp(public / entry)
            )
            for entry in entries
        )
        files = {
            path.relative_to(public)
            for path in (public / directory).rglob("*")
            if path.is_file() and restored.get(str(path.relative_to(public))) != self.island_file_stamp(path)
        }
        if recovering:
            files.update(path.relative_to(snapshot) for path in (snapshot / directory).rglob("*") if path.is_file())
        return files

    @staticmethod
    def is_in_island_output(relative: Path) -> bool:
        return any(
            relative.is_relative_to(directory) and ".." not in relative.parts for directory in ISLAND_PATHS
        )

    def restore_missing_bundles(self, public: Path, snapshot: Path, scope: Path) -> dict:
        restored = {}
        for saved in (snapshot / scope).rglob("*"):
            relative = saved.relative_to(snapshot)
            dest = public / relative
            if not saved.is_file() or dest.resolve() != dest or dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=dest.parent) as copy:
                shutil.copy2(saved, copy.name)
                dest.hardlink_to(copy.name)
            if self.is_in_island_output(relative):
                restored[str(relative)] = self.island_file_stamp(dest)
        return restored

    @staticmethod
    def island_file_stamp(path: Path) -> list[int] | None:
        # copy2 preserves these fields when a Frappe archive carries the bench's islands forward.
        try:
            metadata = path.stat()
        except FileNotFoundError:
            return None
        return [metadata.st_mtime_ns, metadata.st_size]

    @staticmethod
    def _has_usable_prebuilt(app: "App", force: bool) -> bool:
        """Whether a published archive could replace a source build."""
        return not force and not app.is_linked and not app.has_source_changes

    def install_prebuilt_assets(self, app: "App", skip_frontend: bool = False) -> bool:
        """Install the app's published assets and build what the archive lacks. False when none are published."""
        from pilot.core.app.prebuilt_assets import PrebuiltAssets

        prebuilt = PrebuiltAssets(app)
        if not prebuilt.install():
            return False
        name = app.module_name
        app_public_dir = app.path / name / "public"
        self.setup_prebuilt_assets(
            name, app_public_dir, app_public_dir / "dist", prebuilt.asset_maps, prebuilt.has_page_islands
        )
        if not skip_frontend:
            if app.has_page_islands and not prebuilt.has_page_islands:
                with self.keeping_frappe_page_islands(app):
                    self.build_page_islands()
            if app.has_studio_apps and not prebuilt.has_studio_apps:
                self.build_studio_apps(app)
        else:
            if app.has_page_islands and not prebuilt.has_page_islands:
                print(f"  Skipping page-island build for {app.config.name} (frontend skipped).")
            if app.has_studio_apps and not prebuilt.has_studio_apps:
                print(f"  Skipping Studio app build for {app.config.name} (frontend skipped).")
        return True

    def build_app_from_source(self, app: "App") -> None:
        """Build the app's assets from source."""
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
        if NodePackages(app.path).has_script("build"):
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
        """Link the prebuilt assets of an app."""
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
        script = self.page_island_script
        if not script.exists():
            return
        NodePackages(script.parent / "toolchain").ensure_installed()
        print("  Building Frappe UI page islands...")
        sys.stdout.flush()
        # Frappe's script runs yarn, which a task's PATH lacks when yarn lives in ~/.local/bin.
        self.run_compiler(
            ["node", str(script), "--production"],
            cwd=frappe_path,
            env=self.manager._build_env(),
            stream_output=True,
        )

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
            [*self.bench.python_argv, "-c", build],
            cwd=self.bench.sites_path,
            env=self.manager._build_env(),
            stream_output=True,
        )

    @property
    def studio_editor(self) -> NodePackages:
        """The Studio editor packages."""
        return NodePackages(self.bench.apps_path / "frappe" / "studio" / "frontend")

    def write_assets_json(self, app_name: str, dist_dir: Path, assets_dir: Path) -> None:
        """Write the assets.json for an app."""
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
        """The bundle names mapped to their built files."""
        bundle_dir = dist_dir / directory
        if not bundle_dir.is_dir():
            return {}

        entries = {}
        for path in sorted(bundle_dir.iterdir()):
            match = PythonAssetBuilder.bundle_re.match(path.name)
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
            existing = read_json_object(path)
            if replacing:
                existing = {
                    key: url
                    for key, url in existing.items()
                    if not str(url).startswith(replacing) or (keeping and str(url).startswith(keeping))
                }
            existing.update(new_entries)
            replace_private_text_locked(path, json.dumps(existing, indent="\t", sort_keys=True) + "\n")
