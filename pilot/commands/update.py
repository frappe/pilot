from __future__ import annotations

import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
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

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, skip_confirm=args.yes, apps=set(args.apps) if args.apps else None)

    def __init__(
        self,
        bench: "Bench",
        skip_confirm: bool = False,
        apps: set | None = None,
        task_log: Path | None = None,
    ) -> None:
        self.bench = bench
        self.skip_confirm = skip_confirm
        self._apps_filter = apps  # None = all apps
        self._task_log = task_log
        self.tag: str | None = None
        self._current_step: str | None = None

    def _step(self, key: str, label: str) -> None:
        self._current_step = key
        print(f"STEP {key},{time.time():.3f} {label}", flush=True)

    def _step_failed(self) -> None:
        if self._current_step:
            print(f"STEP-FAILED {self._current_step},{time.time():.3f}", flush=True)

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
            self.bench.reload_workers()
        except MigrateError:
            self._step_failed()
            traceback.print_exc()  # print at the point of failure, before any rollback steps
            sys.stdout.flush()
            if volume_enabled and self.tag:
                self._step("post", "Rolling back to snapshot")
                self._rollback_preserving_log()
                self._step("restart", "Restarting services after rollback")
                self.bench.reload_workers()
            raise
        finally:
            if volume_enabled:
                self.bench.set_maintenance_mode(False)

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

    def _snapshot(self):
        from datetime import datetime

        from pilot.managers.snapshot_orchestrator import get_orchestrator

        self.tag = datetime.now().strftime("%Y%m%d-%H%M%S")  # Dynamically set tag for rollbacks
        try:
            orchestrator = get_orchestrator(self.bench.path)
            orchestrator.create_snapshot(self.tag)
            print(f"Bench snapshot {self.tag} taken")
        except Exception as e:
            print(f" Unable to take snapshot for automatic rollbacks: {e}")

    def _rollback(self):
        from pilot.managers.snapshot_orchestrator import get_orchestrator

        try:
            orchestrator = get_orchestrator(self.bench.path)
            orchestrator.rollback_snapshot(self.tag)
            print(f"Successfully rolled back to {self.tag}")
        except Exception as e:
            print(f" Unable to rollback to snapshot: {e}")

    def _rollback_preserving_log(self) -> None:
        """Roll back while keeping the full task log across the pool revert.

        Rollback reverts the volume — including this task's output.log — to the
        pre-update snapshot, which would erase everything logged so far. To keep
        the complete log we:
          1. copy the current log to a /tmp file (outside the pool),
          2. send the rollback step's own output to that /tmp file so it survives
             the revert,
          3. after the revert, rewrite the preserved log into a fresh output.log
             and resume logging there.
        """
        if not self._task_log:
            self._rollback()
            return

        tmp = Path("/tmp") / f"bench-update-rollback-{self.tag}.log"

        # 1. Preserve everything logged up to and including the "post" step.
        try:
            sys.stdout.flush()
            sys.stderr.flush()
            tmp.write_bytes(self._task_log.read_bytes())
        except Exception:
            tmp = None  # fall back to plain rollback if we can't preserve

        # 2. Run the rollback, capturing its output into /tmp so it survives the revert.
        if tmp is not None:
            with open(tmp, "a") as sink, redirect_stdout(sink), redirect_stderr(sink):
                self._rollback()
        else:
            self._rollback()

        # 3. Rewrite the full preserved log into a fresh output.log and resume there.
        if tmp is not None:
            try:
                self._task_log.parent.mkdir(parents=True, exist_ok=True)
                restored = open(self._task_log, "w", encoding="utf-8")
                restored.write(tmp.read_text(encoding="utf-8", errors="replace"))
                restored.flush()
                sys.stdout = restored
                sys.stderr = restored
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

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
                site.migrate()
            except CommandError as e:
                raise MigrateError(f"Migration failed for {site.config.name}") from e

