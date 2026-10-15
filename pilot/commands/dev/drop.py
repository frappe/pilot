from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import BenchMode, Command


@dataclass(kw_only=True)
class DevDropCommand(Command):
    name: ClassVar[str] = "drop"
    help: ClassVar[str] = "Delete the app's bench and site. The repository is not touched."
    group: ClassVar[str] = "dev"
    bench_mode: ClassVar[BenchMode] = BenchMode.NONE

    skip_confirm: bool = False

    def run(self) -> None:
        from pilot.core.workspace import Workspace

        workspace = Workspace.current()
        bench = workspace.bench
        self.confirm(
            f"Permanently delete bench '{bench.config.name}' and its site data?",
            skip=self.skip_confirm,
        )
        workspace.drop(on_progress=self.report)
