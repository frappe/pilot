from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class RestartCommand(Command):
    name: ClassVar[str] = "restart"
    help: ClassVar[str] = "Restart the production workload (production mode only)."
    supports_all_benches: ClassVar[bool] = True

    admin: Annotated[bool, Arg(help="Also restart the admin control plane, if it's installed.")] = False

    def run(self) -> None:
        self.bench.restart_workload(include_admin=self.admin, on_progress=self.report)


def _incomplete_message(bench) -> str:
    from pilot.core.bench.runtime import incomplete_production_message

    return incomplete_production_message(bench)
