from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import Command


@dataclass(kw_only=True)
class RemoveCentralCommand(Command):
    name: ClassVar[str] = "central"
    help: ClassVar[str] = "Disable Central management and remove its VM hostname aliases."
    group: ClassVar[str] = "remove"

    def run(self) -> None:
        """Disable Central management."""
        self.bench.remove_central(on_progress=self.report)
        self.report("Central is disabled for this host.")
