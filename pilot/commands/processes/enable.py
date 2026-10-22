from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class EnableProcesses(Command):
    name: ClassVar[str] = "enable"
    group: ClassVar[str] = "processes"
    help: ClassVar[str] = "Turn disabled app processes back on."

    names: Annotated[
        list[str], Arg(help="App process names, such as mail-stalwart, or app names.", metavar="NAME")
    ]

    def run(self) -> None:
        enabled = self.bench.processes.enable(self.names)
        self.report(f"Enabled {', '.join(enabled)}. App processes run while a site uses their app.")
        if not self.bench.config.production.enabled:
            self.report("A running dev bench picks this up on the next `pilot start`.")
