from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import BenchMode, Command


@dataclass(kw_only=True)
class InitCommand(Command):
    name: ClassVar[str] = "init"
    help: ClassVar[str] = "Initialise the bench."
    # Heavy/irreversible - never guess the target bench.
    bench_mode: ClassVar[BenchMode] = BenchMode.EXPLICIT

    def run(self) -> None:
        self.bench.initialize(on_progress=self.report)
