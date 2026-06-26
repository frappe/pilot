from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from pilot.commands.base import Command

if TYPE_CHECKING:
    from pilot.core.bench import Bench
    from pilot.managers.supervisor_process_manager import SupervisorProcessManager
    from pilot.managers.systemd_process_manager import SystemdProcessManager

_DEV_MESSAGE = (
    "Restart is available only for production benches managed by\n"
    "systemd, Supervisor, or OpenRC.\n\n"
    "For development, stop the runner and execute `bench start` again."
)

class RestartCommand(Command):
    name = "restart"
    help = "Restart the production workload (production mode only)."
    supports_all_benches = True

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--admin",
            action="store_true",
            help="Also restart the admin control plane, if it's installed.",
        )

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, admin=args.admin)

    def __init__(self, bench: "Bench", admin: bool = False) -> None:
        self.bench = bench
        self.admin = admin

    def run(self) -> None:
        if not self.bench.config.production.enabled:
            print(_DEV_MESSAGE)
            return

        from pilot.managers.process_manager import ProcessManagerFactory

        manager: SystemdProcessManager | SupervisorProcessManager = ProcessManagerFactory.create(self.bench)
        if not manager.is_configured():
            print(_incomplete_message(self.bench))
            return

        manager.generate_config()
        manager.reload()
        manager.restart()
        if self.admin:
            manager.restart_admin()


def _incomplete_message(bench: "Bench") -> str:
    pm = bench.config.production.process_manager
    return (
        f"Bench {bench.config.name} is configured for production, but its {pm}\n"
        f"deployment is incomplete.\n\n"
        f"Repair it with:\n"
        f"  bench -b {bench.config.name} setup production"
    )
