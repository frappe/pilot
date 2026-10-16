from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar

from pilot.commands import Arg, BenchMode, Command


@dataclass(kw_only=True)
class PrepareTemplateCommand(Command):
    name: ClassVar[str] = "prepare-template"
    help: ClassVar[str] = "Save a development fixture site and its app revisions for bench forks."
    bench_mode: ClassVar[BenchMode] = BenchMode.EXPLICIT

    site: Annotated[str, Arg(help="Fixture site to back up.")]
    output: Annotated[Path, Arg(help="New directory for the reusable template.")]

    def run(self) -> None:
        self.bench.site(self.site).prepare_template(self.output, self.report)
