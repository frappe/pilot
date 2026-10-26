import secrets
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
    admin_password: Annotated[
        str | None, Arg(help="New site's Administrator password; generated if omitted.")
    ] = None

    def run(self) -> None:
        from pilot.config import BenchConfig
        from pilot.core.bench import Bench

        if self.target_bench:
            BenchConfig.default(self.target_bench).validate()
        destination = Bench(self.bench.path.parent / self.target_bench) if self.target_bench else self.bench
        password = secrets.token_urlsafe(24) if self.admin_password is None else self.admin_password
        self.bench.site(self.source).clone(self.target, destination, password, self.report)
        if self.admin_password is None:
            self.report(f"Generated Administrator password: {password}")
