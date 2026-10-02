from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar

from pilot.commands import Arg, BenchMode, Command
from pilot.exceptions import BenchError


@dataclass(kw_only=True)
class ForkCommand(Command):
    name: ClassVar[str] = "fork"
    help: ClassVar[str] = "Clone a bench and its current site in one command."
    bench_mode: ClassVar[BenchMode] = BenchMode.OPTIONAL

    source: Annotated[
        str | None, Arg(positional=True, help="Source bench; defaults to the current bench.")
    ] = None
    target: Annotated[str, Arg(help="Name for the new bench.")]
    site_template: Annotated[
        Path | None, Arg(help="Optional prepared snapshot using Git worktrees; defaults to a fresh copy.")
    ] = None
    site: Annotated[str, Arg(help="Source site; required only when the bench has multiple sites.")] = ""

    def run(self) -> None:
        from pilot.core.server import Server

        source = Server().bench(self.source) if self.source else self.bench
        if source is None:
            raise BenchError("Run pilot fork TARGET inside a bench, or pass a source bench or -b NAME.")
        destination = source.fork(self.target, self.site_template, self.report, site=self.site)
        print(
            json.dumps(
                {
                    "bench": destination.config.name,
                    "path": str(destination.path.resolve()),
                    "site": f"{self.target}.localhost",
                    "url": f"http://{self.target}.localhost:{destination.config.http_port}",
                    "start": f"pilot -b {self.target} start",
                    "branches": {app.config.name: app.config.branch for app in destination.apps()},
                }
            )
        )
