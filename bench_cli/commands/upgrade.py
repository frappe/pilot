from __future__ import annotations

from typing import TYPE_CHECKING

from bench_cli.utils import run_command
from bench_cli.commands.admin import download_admin_frontend, _cli_root

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class UpgradeCommand:
    def __init__(self, bench: "Bench | None" = None) -> None:
        self.bench = bench

    def run(self) -> None:
        cli_root = _cli_root()

        print("Pulling latest bench-cli...")
        run_command(["git", "-C", str(cli_root), "pull"], stream_output=True)

        print("Downloading latest admin frontend...")
        if not download_admin_frontend(cli_root):
            print("  Download failed. Run 'bench build-admin' to build from source.")
        else:
            print("bench-cli upgraded successfully.")

        self._restart_if_production()

    def _restart_if_production(self) -> None:
        if not self.bench:
            return
        try:
            from bench_cli.managers.process_manager import ProcessManager, ProcessManagerFactory

            manager = ProcessManagerFactory.detect_running(self.bench)
            if type(manager) is ProcessManager:
                return
            print("Restarting bench processes...")
            manager.restart()
        except Exception:
            pass
