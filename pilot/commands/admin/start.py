from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar

from pilot.commands.base import Arg, BenchMode, Command


def download_admin_frontend(cli_root: Path) -> bool:
    from pilot.core.admin_frontend import download_admin_frontend as _download

    return _download(cli_root)


@dataclass(kw_only=True)
class BuildAdminCommand(Command):
    name: ClassVar[str] = "build-admin"
    help: ClassVar[str] = "Download or rebuild admin frontend assets."
    bench_mode: ClassVar[BenchMode] = BenchMode.NONE

    force: Annotated[bool, Arg(help="Skip download and build from source.")] = False

    def run(self) -> None:
        from pilot.core.admin_frontend import build_admin_frontend

        build_admin_frontend(self.force, on_progress=self.print)
