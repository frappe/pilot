from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import BenchMode, Command


@dataclass(kw_only=True)
class DevInfoCommand(Command):
    name: ClassVar[str] = "info"
    help: ClassVar[str] = "Show the bench behind the app in the current directory."
    group: ClassVar[str] = "dev"
    bench_mode: ClassVar[BenchMode] = BenchMode.NONE

    def run(self) -> None:
        from pilot.core.workspace import Workspace

        workspace = Workspace.current()
        bench = workspace.bench
        rows = {
            "App": f"{workspace.app_name} ({workspace.root})",
            "Bench": f"{bench.config.name} ({bench.path})",
            "Frappe": bench.config.framework_app.branch,
            "Database": bench.config.db_type,
            "Site": workspace.site_url,
        }
        for label, value in rows.items():
            self.report(f"{label:<10}{value}")
