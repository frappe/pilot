from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import BenchMode, Command


@dataclass(kw_only=True)
class UpgradeCommand(Command):
    name: ClassVar[str] = "upgrade"
    help: ClassVar[str] = "Pull latest bench-cli and download the admin frontend."
    # OPTIONAL: used only to restart processes in production, if one is active.
    bench_mode: ClassVar[BenchMode] = BenchMode.OPTIONAL

    def run(self) -> None:
        from pilot.commands.admin.start import download_admin_frontend
        from pilot.utils import cli_root, run_command

        root = cli_root()

        self.report("Pulling latest bench-cli...")
        run_command(["git", "-C", str(root), "pull"], stream_output=True)

        self.report("Installing admin Python dependencies...")
        from pilot.managers.environment import AdminEnvManager

        AdminEnvManager(root).install_python_deps()

        self.report("Downloading latest admin frontend...")
        if not download_admin_frontend(root):
            self.report("  Download failed. Run 'bench build-admin' to build from source.")
        else:
            self.report("bench-cli upgraded successfully.")

        self._restart_if_production()

    def _restart_if_production(self) -> None:
        if not self.bench:
            return
        try:
            from pilot.managers.processes.local import ProcessManager

            manager = ProcessManager.detect_running(self.bench)
            if type(manager) is ProcessManager:
                return
            self.report("Restarting bench processes...")
            manager.restart()
        except Exception as exc:
            logging.debug("Post-upgrade process restart failed: %s", exc)
