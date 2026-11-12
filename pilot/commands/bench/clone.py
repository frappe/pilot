from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class CloneBenchCommand(Command):
    name: ClassVar[str] = "clone-bench"
    help: ClassVar[str] = "Clone bench apps from their default branches, without sites."

    target: Annotated[str, Arg(help="Name of the new bench.")]
    branch: Annotated[
        str, Arg(help="default (origin's default), current (including local edits), or a branch name.")
    ] = "default"
    app_branches: Annotated[str, Arg(help="Comma-separated app=branch overrides.")] = ""

    def run(self) -> None:
        from pilot.core.bench.cloning.branches import parse_app_branches

        destination = self.bench.clone(
            self.target, self.report, branch=self.branch, app_branches=parse_app_branches(self.app_branches)
        )
        self.report(f"Created {destination.path}. Start with: pilot -b {self.target} start")
