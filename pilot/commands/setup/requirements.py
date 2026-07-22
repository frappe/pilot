from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import Command


@dataclass(kw_only=True)
class SetupRequirementsCommand(Command):
    name: ClassVar[str] = "requirements"
    help: ClassVar[str] = "Install Python and JS requirements for all apps."
    group: ClassVar[str] = "setup"

    def run(self) -> None:
        self.bench.install_requirements(on_progress=self.report)
