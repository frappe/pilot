from __future__ import annotations

from typing import TYPE_CHECKING

from pilot.commands.base import Command

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class StopCommand(Command):
    name = "stop"
    help = "Stop the running bench."
    supports_all_benches = True

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def run(self) -> None:
        from pilot.managers.process_manager import ProcessManagerFactory

        manager = ProcessManagerFactory.detect_running(self.bench)
        manager.stop()
        manager.stop_admin()
        print(f"Stopped bench {self.bench.config.name}.")
