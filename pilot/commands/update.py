from __future__ import annotations

import sys
import time
import traceback
from typing import TYPE_CHECKING

from pilot.commands.base import Command
from pilot.exceptions import CommandError, MigrateError

if TYPE_CHECKING:
    from pilot.core.app import App, RevisionPin
    from pilot.core.bench import Bench


class UpdateCommand(Command):
    name = "update"
    help = "Pull latest code and migrate sites."

    @classmethod
    def add_arguments(cls, parser):
        parser.add_argument(
            "--apps",
            nargs="+",
            metavar="APP",
            help="Limit git pull + reinstall to these apps (default: all).",
        )
        parser.add_argument(
            "--skip-failing-patches",
            action="store_true",
            help="Skip patches that fail to run during site migration.",
        )

    @classmethod
    def from_args(cls, args, bench):
        return cls(
            bench,
            skip_confirm=args.yes,
            apps=set(args.apps) if args.apps else None,
            skip_failing_patches=args.skip_failing_patches,
        )

    def __init__(
        self,
        bench: "Bench",
        skip_confirm: bool = False,
        apps: set | None = None,
        skip_failing_patches: bool = False,
    ) -> None:
        self.bench = bench
        self.skip_confirm = skip_confirm
        self._apps_filter = apps  # None = all apps
        self._skip_failing_patches = skip_failing_patches
        self._current_step: str | None = None

    def _step(self, key: str, label: str) -> None:
        self._current_step = key
        print(f"STEP {key},{time.time():.3f} {label}", flush=True)

    def _step_failed(self) -> None:
        if self._current_step:
            print(f"STEP-FAILED {self._current_step},{time.time():.3f}", flush=True)

    def run(self) -> None:
        self._warn_if_running()
        try:
            self._step("fetch", "Fetching latest code")
            self._update_apps()
            self._step("install", "Installing dependencies")
            self._reinstall_apps()
            self._step("assets", "Building assets")
            self._rebuild_assets()
            self._step("migrate", "Migrating sites")
            self._migrate_sites()
            self._step("restart", "Restarting services")
            self.bench.reload_workers()
        except MigrateError:
            self._step_failed()
            traceback.print_exc()  # print at the point of failure, before any rollback steps
            sys.stdout.flush()
            raise

        self._step("done", "Done")

    def _warn_if_running(self) -> None:
        from pilot.managers.process_manager import ProcessManager

        if not ProcessManager.for_bench(self.bench).is_running():
            return
        print("Warning: bench processes appear to be running. Updating while running may cause instability.")
        if not self.skip_confirm:
            try:
                answer = input("Continue anyway? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                raise MigrateError("Aborted.")
            if answer not in ("y", "yes"):
                raise MigrateError("Aborted.")

    def _update_apps(self) -> None:
        from pilot.core.marketplace import Marketplace

        marketplace_by_name = {entry["name"]: entry for entry in Marketplace.registry()}

        for app in self.bench.apps():
            if self._apps_filter is not None and app.config.name not in self._apps_filter:
                continue
            print(f"Updating {app.config.name}...")
            try:
                app.update(pin=self._marketplace_pin(app, marketplace_by_name))
            except CommandError as e:
                print(f"  Error updating {app.config.name}: {e}", file=sys.stderr)
                raise MigrateError(f"Failed to update {app.config.name}")

    @staticmethod
    def _marketplace_pin(app: "App", marketplace_by_name: dict) -> "RevisionPin | None":
        """The marketplace's currently advertised revision pin for this app, if any.

        Matched by the app's installed version against the registry entry's
        targets — this is the marketplace's next intended pin, not
        necessarily the one the app was originally installed at. None for a
        branch target, an app not in the marketplace, or a repo mismatch
        (e.g. a fork) — those keep following the tracked branch.
        """
        entry = marketplace_by_name.get(app.config.name)
        if not entry or app.config.repo != entry.get("repo"):
            return None
        version = app.installed_version
        target = next((t for t in entry.get("targets", []) if t["version"] == version), None)
        if target is None:
            return None

        from pilot.core.app import RevisionPin

        return RevisionPin.from_marketplace_target(target)

    def _reinstall_apps(self) -> None:
        from pilot.managers.python_env_manager import PythonEnvManager

        mgr = PythonEnvManager(self.bench)
        for app in self.bench.apps():
            if self._apps_filter is not None and app.config.name not in self._apps_filter:
                continue
            print(f"Reinstalling {app.config.name}...")
            try:
                mgr.install_app(app)
            except CommandError as e:
                raise MigrateError(f"Failed to install app {app}: {e}")

    def _rebuild_assets(self) -> None:
        from pilot.managers.python_env_manager import PythonEnvManager

        mgr = PythonEnvManager(self.bench)
        for app in self.bench.apps():
            if self._apps_filter is not None and app.config.name not in self._apps_filter:
                continue
            print(f"Updating assets for {app.config.name}...")
            mgr.build_assets_for_app(app)

    def _migrate_sites(self) -> None:
        for site in self.bench.sites():
            print(f"Migrating {site.config.name}...")
            try:
                site.migrate(skip_failing=self._skip_failing_patches)
            except CommandError as e:
                raise MigrateError(f"Migration failed for {site.config.name}") from e
