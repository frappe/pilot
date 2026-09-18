from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class RunCommand(Command):
    name: ClassVar[str] = "start"
    help: ClassVar[str] = "Start all bench processes."
    supports_all_benches: ClassVar[bool] = True
    supports_dev_benches: ClassVar[bool] = True

    detach: Annotated[bool, Arg(help="Run the development bench in the background.")] = False

    def prepare_all_sweep(self) -> str | None:
        if self.bench.config.production.enabled:
            self.detach = False
            return None
        if self.detach:
            return None
        return "development bench; start it in its own terminal, or pass --detach"

    def run(self) -> None:
        if self.detach:
            self.bench.start_detached(on_progress=self.report)
            return
        self.bench.start(on_progress=self.report)
