from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, BenchMode, Command


@dataclass(kw_only=True)
class InitCommand(Command):
    name: ClassVar[str] = "init"
    help: ClassVar[str] = "Initialise the bench."
    # Heavy/irreversible - never guess the target bench.
    bench_mode: ClassVar[BenchMode] = BenchMode.EXPLICIT

    no_dev: Annotated[
        bool,
        Arg(help="Skip apps' dev extras. Stored as bench.install_dev_extra for later installs."),
    ] = False

    def run(self) -> None:
        if self.no_dev:
            self.bench.config.install_dev_extra = False
            self.bench.config.write(self.bench.path)
        self.bench.initialize(on_progress=self.report)
