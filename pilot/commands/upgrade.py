from __future__ import annotations

from typing import TYPE_CHECKING

from pilot.commands.base import Command

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class UpgradeCommand(Command):
    name = "upgrade"
    help = "Pull latest bench-cli and download the admin frontend."
    requires_bench = False

    @classmethod
    def from_args(cls, args, bench):
        # Bench is optional: used only to restart processes in production.
        if bench is None:
            from pilot.loader import load_bench

            try:
                bench = load_bench()
            except Exception:
                bench = None
        return cls(bench)

    def __init__(self, bench: "Bench | None" = None) -> None:
        self.bench = bench

    def run(self) -> None:
        from pilot.commands.admin import download_admin_frontend
        from pilot.loader import cli_root
        from pilot.utils import run_command

        root = cli_root()

        print("Pulling latest bench-cli...")
        run_command(["git", "-C", str(root), "pull"], stream_output=True)

        print("Installing admin Python dependencies...")
        from pilot.managers.admin_env_manager import AdminEnvManager

        AdminEnvManager(root).install_python_deps()

        print("Downloading latest admin frontend...")
        if not download_admin_frontend(root):
            print("  Download failed. Run 'bench build-admin' to build from source.")
        else:
            print("bench-cli upgraded successfully.")

        self._restart_if_production()

    def _restart_if_production(self) -> None:
        if not self.bench:
            return
        try:
            from pilot.managers.process_manager import ProcessManager
            from pilot.managers.process_manager import ProcessManager

            manager = ProcessManager.detect_running(self.bench)
            if type(manager) is ProcessManager:
                return
            print("Restarting bench processes...")
            manager.restart()
        except Exception:
            pass
