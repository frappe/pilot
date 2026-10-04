from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class WorktreeFrappeCommand(Command):
    name: ClassVar[str] = "frappe"
    help: ClassVar[str] = "Run a Frappe CLI command with a worktree's code and sites."
    group: ClassVar[str] = "worktree"

    worktree_name: Annotated[str, Arg(help="Worktree name.", metavar="name")]
    args: tuple[str, ...] = ()

    def run(self) -> None:
        sys.exit(self.bench.worktree(self.worktree_name).frappe(self.args))
