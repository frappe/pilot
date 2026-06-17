from __future__ import annotations

from typing import TYPE_CHECKING

from bench_cli.commands.base import Command

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class StopCommand(Command):
    name = "stop"
    help = "Stop the running bench."

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def run(self) -> None:
        from bench_cli.managers.process_manager import ProcessManagerFactory

        ProcessManagerFactory.detect_running(self.bench).stop()
        print("Bench stopped.")
