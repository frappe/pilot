from __future__ import annotations

from typing import TYPE_CHECKING

from bench_cli.commands.base import Command

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class RestartCommand(Command):
    name = "restart"
    help = "Restart supervisor processes (production mode only)."

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def run(self) -> None:
        from bench_cli.managers.process_manager import ProcessManagerFactory

        manager = ProcessManagerFactory.detect_running(self.bench)
        manager.generate_config()
        manager.reload()
        manager.restart()
