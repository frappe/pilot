from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import Command


@dataclass(kw_only=True)
class RunCommand(Command):
    name: ClassVar[str] = "start"
    help: ClassVar[str] = "Start all bench processes."
    supports_all_benches: ClassVar[bool] = True

    def run(self) -> None:
        self.bench.start(on_progress=self.report)
