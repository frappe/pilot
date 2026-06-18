from __future__ import annotations

from typing import TYPE_CHECKING

from bench_cli.commands.base import Command

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench

_DEV_MESSAGE = (
    "Restart is available only for production benches managed by\n"
    "systemd or Supervisor.\n\n"
    "For development, stop the runner and execute `bench start` again."
)


class RestartCommand(Command):
    name = "restart"
    help = "Restart the production workload (production mode only)."

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def run(self) -> None:
        if not self.bench.config.production.enabled:
            print(_DEV_MESSAGE)
            return

        from bench_cli.managers.process_manager import ProcessManagerFactory

        manager = ProcessManagerFactory.create(self.bench)
        if not manager.is_configured():
            print(_incomplete_message(self.bench))
            return

        manager.generate_config()
        manager.reload()
        manager.restart()


def _incomplete_message(bench: "Bench") -> str:
    pm = bench.config.production.process_manager
    return (
        f"Bench {bench.config.name} is configured for production, but its {pm}\n"
        f"deployment is incomplete.\n\n"
        f"Repair it with:\n"
        f"  bench -b {bench.config.name} setup production"
    )
