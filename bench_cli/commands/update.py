from __future__ import annotations

import sys
import time

from bench_cli.core.bench import Bench
from bench_cli.exceptions import CommandError
from bench_cli.managers.process_manager import ProcessManagerFactory
from bench_cli.managers.python_env_manager import PythonEnvManager


class UpdateCommand:
    def __init__(self, bench: Bench, skip_confirm: bool = False) -> None:
        self.bench = bench
        self.skip_confirm = skip_confirm

    @staticmethod
    def _step(key: str, label: str) -> None:
        print(f"##[step:{key},{time.time():.3f}] {label}", flush=True)

    def run(self) -> None:
        self._warn_if_running()
        self._step("fetch", "Fetching latest code")
        self._update_apps()
        self._step("install", "Installing dependencies")
        self._reinstall_apps()
        self._step("assets", "Building assets")
        self._rebuild_assets()
        self._step("migrate", "Migrating sites")
        self._migrate_sites()
        self._step("restart", "Restarting services")
        ProcessManagerFactory.create(self.bench).reload_web()
        self._step("done", "Done")

    def _warn_if_running(self) -> None:
        if not ProcessManagerFactory.create(self.bench).is_running():
            return
        print(
            "Warning: bench processes appear to be running. "
            "Updating while running may cause instability."
        )
        if not self.skip_confirm:
            try:
                answer = input("Continue anyway? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nAborted.")
                sys.exit(1)
            if answer not in ("y", "yes"):
                print("Aborted.")
                sys.exit(1)

    def _update_apps(self) -> None:
        for app in self.bench.apps():
            print(f"Updating {app.config.name}...")
            try:
                app.update()
            except CommandError as e:
                print(f"  Error updating {app.config.name}: {e}", file=sys.stderr)

    def _reinstall_apps(self) -> None:
        mgr = PythonEnvManager(self.bench)
        for app in self.bench.apps():
            print(f"Reinstalling {app.config.name}...")
            mgr.install_app(app)

    def _rebuild_assets(self) -> None:
        mgr = PythonEnvManager(self.bench)
        for app in self.bench.apps():
            print(f"Updating assets for {app.config.name}...")
            mgr.build_assets_for_app(app)

    def _migrate_sites(self) -> None:
        failed = False
        for site in self.bench.sites():
            print(f"Migrating {site.config.name}...")
            try:
                site.migrate()
            except CommandError as e:
                print(f"  Migration failed for {site.config.name}: {e}", file=sys.stderr)
                failed = True
        if failed:
            sys.exit(1)
