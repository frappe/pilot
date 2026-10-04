from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class AddWorktreeCommand(Command):
    name: ClassVar[str] = "add"
    help: ClassVar[str] = "Check out an app on its own branch with a clone of its site."
    group: ClassVar[str] = "worktree"

    app_name: Annotated[str, Arg(help="App to check out.", metavar="app")]
    worktree_name: Annotated[str, Arg(help="Worktree name; also the default branch.", metavar="name")]
    site: Annotated[str, Arg(help="Site to clone. Defaults to the only site with the app.")] = ""
    branch: Annotated[str, Arg(help="Branch to check out or create. Defaults to the name.")] = ""
    start_point: Annotated[
        str, Arg(help="Where a new branch starts. Defaults to the app's HEAD.", flag="--from", metavar="REF")
    ] = ""

    def run(self) -> None:
        self.bench.add_worktree(
            self.app_name,
            self.worktree_name,
            base_site=self.site,
            branch=self.branch,
            start_point=self.start_point,
            on_progress=self.report,
        )
