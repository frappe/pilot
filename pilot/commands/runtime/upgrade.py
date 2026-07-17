from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar

from pilot.commands.base import BenchMode, Command


@dataclass(kw_only=True)
class UpgradeCommand(Command):
    name: ClassVar[str] = "upgrade"
    help: ClassVar[str] = "Pull latest bench-cli and download the admin frontend."
    # OPTIONAL: used only to restart processes in production, if one is active.
    bench_mode: ClassVar[BenchMode] = BenchMode.OPTIONAL

    def run(self) -> None:
        from pilot.commands.admin.start import download_admin_frontend
        from pilot.loader import cli_root
        from pilot.utils import run_command

        root = cli_root()

        self.print("Pulling latest bench-cli...")
        run_command(["git", "-C", str(root), "pull"], stream_output=True)

        self.print("Installing admin Python dependencies...")
        from pilot.managers.admin_environment import AdminEnvManager

        AdminEnvManager(root).install_python_deps()

        self.print("Downloading latest admin frontend...")
        if not download_admin_frontend(root):
            self.print("  Download failed. Run 'bench build-admin' to build from source.")
        else:
            self.print("bench-cli upgraded successfully.")

        self._restart_if_production()

    def _restart_if_production(self) -> None:
        if not self.bench:
            return
        try:
            from pilot.managers.processes.local import ProcessManager

            manager = ProcessManager.detect_running(self.bench)
            if type(manager) is ProcessManager:
                return
            self.print("Restarting bench processes...")
            manager.restart()
        except Exception as exc:
            logging.debug("Post-upgrade process restart failed: %s", exc)
