from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pilot.commands import BenchMode, Command


def download_admin_frontend(cli_root: Path) -> bool:
    from admin.backend.frontend import download_admin_frontend as _download

    return _download(cli_root)


@dataclass(kw_only=True)
class BuildAdminCommand(Command):
    name: ClassVar[str] = "build-admin"
    help: ClassVar[str] = "Build admin frontend assets from source."
    bench_mode: ClassVar[BenchMode] = BenchMode.NONE

    def run(self) -> None:
        from admin.backend.frontend import build_admin_frontend

        build_admin_frontend(on_progress=self.report)
