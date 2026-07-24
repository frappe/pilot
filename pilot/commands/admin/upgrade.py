from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import BenchMode, Command


@dataclass(kw_only=True)
class UpgradeCommand(Command):
    name: ClassVar[str] = "upgrade"
    group: ClassVar[str] = "admin"
    help: ClassVar[str] = "Update bench-cli to the latest version and restart the admin service."
    # OPTIONAL: used only to restart the admin service in production, if one is active.
    bench_mode: ClassVar[BenchMode] = BenchMode.OPTIONAL

    def run(self) -> None:
        from pilot.updater import perform_upgrade

        perform_upgrade(on_progress=self.report)
        self._restart_admin_if_managed()

    def _restart_admin_if_managed(self) -> None:
        if not self.bench:
            return
        try:
            from pilot.managers.processes.local import ProcessManager

            manager = ProcessManager.detect_running(self.bench)
            if type(manager) is ProcessManager:
                return
            self.report("Restarting admin service...")
            manager.restart_admin()
        except Exception as exc:
            logging.debug("Post-upgrade admin restart failed: %s", exc)
