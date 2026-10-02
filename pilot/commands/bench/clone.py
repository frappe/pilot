from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class CloneBenchCommand(Command):
    name: ClassVar[str] = "clone-bench"
    help: ClassVar[str] = "Copy the current bench's code and dependencies, without sites."

    target: Annotated[str, Arg(help="Name of the new bench.")]

    def run(self) -> None:
        destination = self.bench.clone(self.target, self.report)
        self.report(f"Created {destination.path}. Start with: pilot -b {self.target} start")
