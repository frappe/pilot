from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, BenchMode, Command


@dataclass(kw_only=True)
class InitCommand(Command):
    name: ClassVar[str] = "init"
    help: ClassVar[str] = "Initialise the bench."
    # Heavy/irreversible - never guess the target bench.
    bench_mode: ClassVar[BenchMode] = BenchMode.EXPLICIT

    no_dev: Annotated[
        bool,
        Arg(help="Skip apps' dev extras. Stored as bench.install_dev_extra for later installs."),
    ] = False
    commit: Annotated[
        str, Arg(help="Git commit hash to checkout for the framework app, on its bench.toml branch.")
    ] = ""
    skip_frontend: Annotated[
        bool,
        Arg(
            help="Skip frontend work: no asset builds from source. "
            "Downloads prebuilt assets when published. For test jobs that never serve web pages."
        ),
    ] = False

    def run(self) -> None:
        """Set up the bench."""
        if self.no_dev:
            self.bench.config.install_dev_extra = False
            self.bench.config.write(self.bench.path)
        self.bench.initialize(commit=self.commit, skip_frontend=self.skip_frontend, on_progress=self.report)
        self.report("\nBench initialised. Next steps:")
        self.report("  pilot new-site site1.example.com   # create your first site")
        self.report("  pilot start                        # start all processes")
