from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class RemoveWorktreeCommand(Command):
    name: ClassVar[str] = "remove"
    help: ClassVar[str] = "Remove a worktree, its overlay and its site clone."
    group: ClassVar[str] = "worktree"

    worktree_name: Annotated[str, Arg(help="Worktree name.", metavar="name")]
    delete_branch: Annotated[
        bool, Arg(help="Also delete the branch. Refused if unmerged, unless --force.")
    ] = False
    force: Annotated[bool, Arg(help="Remove even with uncommitted changes.")] = False

    def run(self) -> None:
        self.bench.worktree(self.worktree_name).remove(
            delete_branch=self.delete_branch, force=self.force, on_progress=self.report
        )
        self.report(f"Removed worktree {self.worktree_name}.")
