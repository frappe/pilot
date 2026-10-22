from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class RestartProcesses(Command):
    name: ClassVar[str] = "restart"
    group: ClassVar[str] = "processes"
    help: ClassVar[str] = "Restart processes, or every process of an app (production only)."

    names: Annotated[
        list[str], Arg(help="Process names, such as web or mail-stalwart, or app names.", metavar="NAME")
    ]

    def run(self) -> None:
        restarted = self.bench.processes.restart(self.names)
        self.report(f"Restarted {', '.join(restarted)}.")
