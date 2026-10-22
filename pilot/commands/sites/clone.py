from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class CloneSiteCommand(Command):
    name: ClassVar[str] = "clone-site"
    help: ClassVar[str] = "Copy a site's database and uploads into a fresh site."

    source: Annotated[str, Arg(help="Source site name.")]
    target: Annotated[str, Arg(help="New site name.")]
    target_bench: Annotated[str, Arg(help="Destination bench; defaults to the current bench.")] = ""
    admin_password: Annotated[str, Arg(help="New site's Administrator password.")] = "admin"

    def run(self) -> None:
        from pilot.config import BenchConfig
        from pilot.core.bench import Bench

        if self.target_bench:
            BenchConfig.default(self.target_bench).validate()
        destination = Bench(self.bench.path.parent / self.target_bench) if self.target_bench else self.bench
        self.bench.site(self.source).clone(self.target, destination, self.admin_password, self.report)
