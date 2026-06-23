from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

from bench_cli.commands.base import Command
from bench_cli.exceptions import CommandError, MigrateError
from bench_cli.managers.snapshot_orchestrator import get_orchestrator

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class UpdateCommand(Command):
    name = "update"
    help = "Pull latest code and migrate sites."

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, skip_confirm=args.yes)

    def __init__(
        self,
        bench: "Bench",
        skip_confirm: bool = False,
        apps: set | None = None,
        sites: set | None = None,
        task_log: Path | None = None,
    ) -> None:
        self.bench = bench
        self.skip_confirm = skip_confirm
        self._apps_filter = apps  # None = all apps
        self._sites_filter = sites  # None = all sites
        self._task_log = task_log
        self.tag: str | None = None
        self._current_step: str | None = None

    def _step(self, key: str, label: str) -> None:
        self._current_step = key
        print(f"##[step:{key},{time.time():.3f}] {label}", flush=True)

    def _step_failed(self) -> None:
        if self._current_step:
            print(f"##[step-failed:{self._current_step},{time.time():.3f}]", flush=True)

    def run(self) -> None:
        self._warn_if_running()
        volume_enabled = self.bench.config.volume.enabled
        if volume_enabled:
            self.bench.set_maintenance_mode(True)
            self._step("pre", "Taking a snapshot")
            self._snapshot()
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
            self._reload_web()
        except MigrateError:
            self._step_failed()
            traceback.print_exc()  # print at the point of failure, before any rollback steps
            sys.stdout.flush()
            if volume_enabled and self.tag:
                self._preserve_failure_context()
                self._step("post", "Rolling back to snapshot")
                self._rollback()
                self._restore_task_log()
                self._step("restart", "Restarting services after rollback")
                self._reload_web()
            raise
        finally:
            if volume_enabled:
                self.bench.set_maintenance_mode(False)

        self._step("done", "Done")

    def _warn_if_running(self) -> None:
        from bench_cli.managers.process_manager import ProcessManagerFactory

        if not ProcessManagerFactory.create(self.bench).is_running():
            return
        print("Warning: bench processes appear to be running. Updating while running may cause instability.")
        if not self.skip_confirm:
            try:
                answer = input("Continue anyway? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                raise MigrateError("Aborted.")
            if answer not in ("y", "yes"):
                raise MigrateError("Aborted.")

    def _snapshot(self):
        from datetime import datetime

        self.tag = datetime.now().strftime("%Y%m%d-%H%M%S")  # Dynamically set tag for rollbacks
        try:
            orchestrator = get_orchestrator(self.bench.path)
            orchestrator.create_snapshot(self.tag)
            print(f"Bench snapshot {self.tag} taken")
        except Exception as e:
            print(f" Unable to take snapshot for automatic rollbacks: {e}")

    def _rollback(self):
        try:
            orchestrator = get_orchestrator(self.bench.path)
            orchestrator.rollback_snapshot(self.tag)
        except Exception as e:
            print(f" Unable to rollback to snapshot: {e}")

    def _preserve_failure_context(self) -> None:
        """Snapshot the current task log to /tmp before rollback wipes the ZFS pool."""
        if not self._task_log or not self._task_log.exists():
            return
        path = Path("/tmp") / f"bench-update-failure-{self.tag}.txt"
        try:
            path.write_bytes(self._task_log.read_bytes())
        except Exception:
            pass

    def _restore_task_log(self) -> None:
        """After rollback wipes the task dir, recreate it and write the saved log back.

        Writes via the stdout FD rather than reopening the path, so the FD position
        stays consistent and there are no null-byte gaps from a truncate-then-write.
        """
        if not self._task_log:
            return
        path = Path("/tmp") / f"bench-update-failure-{self.tag}.txt"
        if not path.exists():
            return
        try:
            content = path.read_bytes()
            self._task_log.parent.mkdir(parents=True, exist_ok=True)
            # Truncate and rewrite via the open stdout FD so position stays in sync.
            stdout_fd = sys.stdout.fileno()
            import os
            os.ftruncate(stdout_fd, 0)
            os.lseek(stdout_fd, 0, os.SEEK_SET)
            os.write(stdout_fd, content)
            path.unlink()
        except Exception:
            pass

    def _update_apps(self) -> None:
        for app in self.bench.apps():
            if self._apps_filter is not None and app.config.name not in self._apps_filter:
                continue
            print(f"Updating {app.config.name}...")
            try:
                app.update()
            except CommandError as e:
                print(f"  Error updating {app.config.name}: {e}", file=sys.stderr)
                raise MigrateError(f"Failed to update {app.config.name}")

    def _reinstall_apps(self) -> None:
        from bench_cli.managers.python_env_manager import PythonEnvManager

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
        from bench_cli.managers.python_env_manager import PythonEnvManager

        mgr = PythonEnvManager(self.bench)
        for app in self.bench.apps():
            if self._apps_filter is not None and app.config.name not in self._apps_filter:
                continue
            print(f"Updating assets for {app.config.name}...")
            mgr.build_assets_for_app(app)

    def _migrate_sites(self) -> None:
        for site in self.bench.sites():
            if self._sites_filter is not None and site.config.name not in self._sites_filter:
                continue
            print(f"Migrating {site.config.name}...")
            try:
                raise MigrateError(f"Migration failed for this site: {site.config.name}")
                site.migrate()
            except CommandError as e:
                raise MigrateError(f"Migration failed for {site.config.name}") from e

    def _reload_web(self) -> None:
        from bench_cli.managers.process_manager import ProcessManagerFactory

        try:
            ProcessManagerFactory.create(self.bench).reload_web()
        except Exception as e:
            print(f"Warning: Failed to reload web service: {e}")
