from __future__ import annotations

import sys
import time
import traceback
from typing import TYPE_CHECKING

from pilot.commands.base import Command
from pilot.exceptions import MigrateError

if TYPE_CHECKING:
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

    def run(self) -> None:
        self._warn_if_running()
        try:
            self.bench.update(
                apps_filter=self._apps_filter,
                skip_failing_patches=self._skip_failing_patches,
                on_step=self._step,
                on_progress=self.report,
            )
        except MigrateError:
            self._step_failed()
            traceback.print_exc()  # print at the point of failure, before any rollback steps
            sys.stdout.flush()
            raise

    def _step(self, key: str, label: str) -> None:
        self._current_step = key
        self.report(f"STEP {key},{time.time():.3f} {label}")

    def _step_failed(self) -> None:
        if self._current_step:
            self.report(f"STEP-FAILED {self._current_step},{time.time():.3f}")

    def _warn_if_running(self) -> None:
        from pilot.managers.processes.local import ProcessManager

        if not ProcessManager.for_bench(self.bench).is_running():
            return
        self.report("Warning: bench processes appear to be running. Updating while running may cause instability.")
        self.confirm("Continue anyway?", skip=self.skip_confirm, error=MigrateError)
