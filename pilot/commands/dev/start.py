from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import BenchMode, Command


@dataclass(kw_only=True)
class DevStartCommand(Command):
    name: ClassVar[str] = "start"
    help: ClassVar[str] = "Run the app's bench in the foreground until Ctrl-C."
    group: ClassVar[str] = "dev"
    bench_mode: ClassVar[BenchMode] = BenchMode.NONE

    def run(self) -> None:
        from pilot.core.workspace import Workspace

        Workspace.current().start(on_progress=self.report)
