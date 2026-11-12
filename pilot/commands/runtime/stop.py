from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import Command


@dataclass(kw_only=True)
class StopCommand(Command):
    name: ClassVar[str] = "stop"
    help: ClassVar[str] = "Stop the running bench."
    supports_all_benches: ClassVar[bool] = True

    def run(self) -> None:
        """Stop the bench."""
        self.bench.stop(on_progress=self.report)
