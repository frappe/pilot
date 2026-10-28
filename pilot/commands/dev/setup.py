from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar, Literal

from pilot.commands import Arg, BenchMode, Command


@dataclass(kw_only=True)
class DevSetupCommand(Command):
    name: ClassVar[str] = "setup"
    help: ClassVar[str] = (
        "Set up a bench for the app in the current directory, or bring it up to date. Safe to run again."
    )
    group: ClassVar[str] = "dev"
    bench_mode: ClassVar[BenchMode] = BenchMode.NONE

    database: Annotated[
        Literal["sqlite", "mariadb", "postgres"] | None,
        Arg(help="Database engine for a new bench (default: sqlite)."),
    ] = None
    frappe_branch: Annotated[
        str | None,
        Arg(help="Frappe branch for a new bench (default: the checked-out version-N branch, or develop)."),
    ] = None

    def run(self) -> None:
        from pilot.core.workspace import Workspace

        Workspace.current().setup(self.database, self.frappe_branch, on_progress=self.report)
        self.report("\nReady. Start the dev server with: pilot dev start")
