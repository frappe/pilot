from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, ClassVar

from pilot.commands.base import Arg, Command

if TYPE_CHECKING:
    from pilot.core.bench import Bench

_DEV_MESSAGE = (
    "Restart is available only for production benches managed by\n"
    "systemd or Supervisor.\n\n"
    "For development, stop the runner and execute `bench start` again."
)


@dataclass(kw_only=True)
class RestartCommand(Command):
    name: ClassVar[str] = "restart"
    help: ClassVar[str] = "Restart the production workload (production mode only)."
    supports_all_benches: ClassVar[bool] = True

    admin: Annotated[bool, Arg(help="Also restart the admin control plane, if it's installed.")] = False

    def run(self) -> None:
        if not self.bench.config.production.enabled:
            self.print(_DEV_MESSAGE)
            return

        from typing import cast

        from pilot.managers.processes.local import ProcessManager
        from pilot.managers.processes.base import ManagedProcessManager

        # production.enabled is already confirmed above, so for_bench() always
        # returns a ManagedProcessManager subclass here, never the plain base.
        manager = cast(ManagedProcessManager, ProcessManager.for_bench(self.bench))
        if not manager.is_configured():
            self.print(_incomplete_message(self.bench))
            return

        manager.write_config()
        manager.reload_manager_config()
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
