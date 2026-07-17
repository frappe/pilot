from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands.base import Command


@dataclass(kw_only=True)
class StopCommand(Command):
    name: ClassVar[str] = "stop"
    help: ClassVar[str] = "Stop the running bench."
    supports_all_benches: ClassVar[bool] = True

    def run(self) -> None:
        from pilot.managers.processes.local import ProcessManager

        manager = ProcessManager.detect_running(self.bench)
        manager.stop()
        manager.stop_admin()
        self.print(f"Stopped bench {self.bench.config.name}.")
