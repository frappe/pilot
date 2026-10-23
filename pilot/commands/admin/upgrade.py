from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import BenchMode, Command


@dataclass(kw_only=True)
class UpgradeCommand(Command):
    name: ClassVar[str] = "upgrade"
    group: ClassVar[str] = "admin"
    help: ClassVar[str] = "Update Pilot to the latest version and restart the admin service."
    # OPTIONAL: used only to restart the admin service in production, if one is active.
    bench_mode: ClassVar[BenchMode] = BenchMode.OPTIONAL

    def run(self) -> None:
        from pilot.updater import perform_upgrade, restart_admin_with_new_code

        perform_upgrade(on_progress=self.report)
        if self.bench:
            restart_admin_with_new_code(self.bench.path)
