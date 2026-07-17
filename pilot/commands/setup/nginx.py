from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands.base import Command


@dataclass(kw_only=True)
class SetupNginxCommand(Command):
    name: ClassVar[str] = "nginx"
    help: ClassVar[str] = "Generate nginx config."
    group: ClassVar[str] = "setup"

    def run(self) -> None:
        self.bench.setup_nginx(on_progress=self.print)
