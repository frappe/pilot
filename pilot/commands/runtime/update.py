from __future__ import annotations

import sys
import time
import traceback
from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands.base import Arg, Command
from pilot.exceptions import MigrateError


@dataclass(kw_only=True)
class UpdateCommand(Command):
    name: ClassVar[str] = "update"
    help: ClassVar[str] = "Pull latest code and migrate sites."

    skip_confirm: bool = False
    apps: Annotated[
        list[str] | None,
        Arg(help="Limit git pull + reinstall to these apps (default: all).", metavar="APP"),
    ] = None
    skip_failing_patches: Annotated[bool, Arg(help="Skip patches that fail to run during site migration.")] = False

    def __post_init__(self) -> None:
        self._apps_filter = set(self.apps) if self.apps else None  # None = all apps
        self._current_step: str | None = None

    def run(self) -> None:
        self._warn_if_running()
        try:
            self.bench.update(
                apps_filter=self._apps_filter,
                skip_failing_patches=self.skip_failing_patches,
                on_step=self._step,
                on_progress=self.print,
            )
        except MigrateError:
            self._step_failed()
            traceback.print_exc()  # print at the point of failure, before any rollback steps
            sys.stdout.flush()
            raise

    def _step(self, key: str, label: str) -> None:
        self._current_step = key
        self.print(f"STEP {key},{time.time():.3f} {label}")

    def _step_failed(self) -> None:
        if self._current_step:
            self.print(f"STEP-FAILED {self._current_step},{time.time():.3f}")

    def _warn_if_running(self) -> None:
        from pilot.managers.processes.local import ProcessManager

        if not ProcessManager.for_bench(self.bench).is_running():
            return
        self.print("Warning: bench processes appear to be running. Updating while running may cause instability.")
        self.confirm("Continue anyway?", skip=self.skip_confirm, error=MigrateError)
