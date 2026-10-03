from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class StopWorktreeCommand(Command):
    name: ClassVar[str] = "stop"
    help: ClassVar[str] = "Stop a running worktree. The main bench keeps running."
    group: ClassVar[str] = "worktree"

    worktree_name: Annotated[str, Arg(help="Worktree name.", metavar="name")]

    def run(self) -> None:
        self.bench.worktree(self.worktree_name).stop()
        self.report(f"Stopped worktree {self.worktree_name}.")
