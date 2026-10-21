from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class ListProcesses(Command):
    name: ClassVar[str] = "ls"
    aliases: ClassVar[tuple[str, ...]] = ("list",)
    group: ClassVar[str] = "processes"
    help: ClassVar[str] = "List the bench and app processes with their state."

    app: Annotated[str, Arg(help="Only this app's declared processes, used by a site or not.")] = ""

    def run(self) -> None:
        rows = self.bench.processes.get_rows(self.app or None)
        if not rows:
            self.report(f"App '{self.app}' declares no processes." if self.app else "No processes.")
            return

        table = [("NAME", "APP", "STATE", "PID", "COMMAND")]
        table += [
            (row.name, row.app, row.state.state, str(row.state.pid or "-"), row.command) for row in rows
        ]
        widths = [max(len(line[column]) for line in table) for column in range(4)]
        for name, app, state, pid, command in table:
            self.report(
                f"{name:<{widths[0]}}  {app:<{widths[1]}}  {state:<{widths[2]}}  {pid:<{widths[3]}}  {command}"
            )
