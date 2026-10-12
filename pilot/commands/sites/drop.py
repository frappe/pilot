from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, BenchMode, Command
from pilot.exceptions import BenchError
from pilot.internal.tasks.store import TaskStore


@dataclass(kw_only=True)
class DropSiteCommand(Command):
    name: ClassVar[str] = "drop-site"
    help: ClassVar[str] = "Back up and remove a site. Use pilot --yes to skip confirmation."
    bench_mode: ClassVar[BenchMode] = BenchMode.EXPLICIT

    site_name: Annotated[str, Arg(help="Site name.", metavar="site")]
    no_backup: Annotated[bool, Arg(help="Remove the site without a backup.")] = False
    skip_confirm: bool = False

    def run(self) -> None:
        if self.site_name not in {site.config.name for site in self.bench.sites()}:
            raise BenchError(f"Site '{self.site_name}' does not exist in this bench.")
        self.confirm(f"Permanently delete site '{self.site_name}'?", skip=self.skip_confirm)
        with TaskStore(self.bench.path).reserve_direct_site(self.site_name):
            self.bench.site(self.site_name).drop(on_progress=self.report, no_backup=self.no_backup)
