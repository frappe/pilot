from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class StartWorktreeCommand(Command):
    name: ClassVar[str] = "start"
    help: ClassVar[str] = "Run a worktree's processes in the foreground."
    group: ClassVar[str] = "worktree"

    worktree_name: Annotated[str, Arg(help="Worktree name.", metavar="name")]

    def run(self) -> None:
        self.bench.worktree(self.worktree_name).start(on_progress=self.report)
