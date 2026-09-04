from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command
from pilot.tasks.switch_branch import SwitchBranchTask


@dataclass(kw_only=True)
class SwitchBranchCommand(Command):
    name: ClassVar[str] = "switch-branch"
    help: ClassVar[str] = "Switch an installed app to another git branch."

    app_name: Annotated[str, Arg(help="App name.", metavar="app")]
    branch: Annotated[str, Arg(help="Git branch to switch to.", metavar="branch")]

    def run(self) -> None:
        task = SwitchBranchTask(
            bench=self.bench, bench_root=self.bench.path, name=self.app_name, branch=self.branch
        )
        task.run()
        # Queued runs reload workers via the task's on_success callback;
        # an inline run has no runner, so reload here.
        self.bench.reload_workers()
