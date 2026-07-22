from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import Command


@dataclass(kw_only=True)
class RemoveProductionCommand(Command):
    name: ClassVar[str] = "production"
    help: ClassVar[str] = "Remove a production deployment (keeps logs, certificates, admin domain)."
    group: ClassVar[str] = "remove"

    def run(self) -> None:
        self.bench.remove_production(on_progress=self.report)
