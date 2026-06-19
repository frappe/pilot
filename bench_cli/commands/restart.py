from __future__ import annotations

from typing import TYPE_CHECKING

from bench_cli.commands.base import Command

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench
    from bench_cli.managers.supervisor_process_manager import SupervisorProcessManager
    from bench_cli.managers.systemd_process_manager import SystemdProcessManager

_DEV_MESSAGE = "Restart is available only for production benches managed by\nsystemd or Supervisor.\n\nFor development, stop the runner and execute `bench start` again."


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

        manager: SystemdProcessManager | SupervisorProcessManager = ProcessManagerFactory.create(self.bench)
        if not manager.is_configured():
            print(_incomplete_message(self.bench))
            return

        manager.generate_config()
        manager.reload()
        manager.restart()


def _incomplete_message(bench: "Bench") -> str:
    pm = bench.config.production.process_manager
    return f"Bench {bench.config.name} is configured for production, but its {pm}\ndeployment is incomplete.\n\nRepair it with:\n  bench -b {bench.config.name} setup production"
