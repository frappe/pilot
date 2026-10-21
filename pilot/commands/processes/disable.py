from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class DisableProcesses(Command):
    name: ClassVar[str] = "disable"
    group: ClassVar[str] = "processes"
    help: ClassVar[str] = "Stop app processes and keep them off."

    names: Annotated[
        list[str], Arg(help="App process names, such as mail-stalwart, or app names.", metavar="NAME")
    ]

    def run(self) -> None:
        disabled = self.bench.processes.disable(self.names)
        self.report(f"Disabled {', '.join(disabled)}.")
        if not self.bench.config.production.enabled:
            self.report("A running dev bench picks this up on the next `pilot start`.")
