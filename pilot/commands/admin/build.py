from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import BenchMode, Command


@dataclass(kw_only=True)
class BuildAdminCommand(Command):
    name: ClassVar[str] = "build"
    group: ClassVar[str] = "admin"
    help: ClassVar[str] = "Rebuild admin frontend assets from source."
    bench_mode: ClassVar[BenchMode] = BenchMode.NONE

    def run(self) -> None:
        from admin.backend.frontend import build_admin_frontend

        build_admin_frontend(on_progress=self.report)
