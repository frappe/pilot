from __future__ import annotations

from pilot.commands.base import Command


class UpgradeCommand(Command):
    name = "upgrade"
    help = "Pull latest bench-cli and download the admin frontend."
    requires_bench = False
    optional_bench = True  # used only to restart processes in production

    def run(self) -> None:
        from pilot.commands.admin.start import download_admin_frontend
        from pilot.loader import cli_root
        from pilot.utils import run_command

        root = cli_root()

        print("Pulling latest bench-cli...")
        run_command(["git", "-C", str(root), "pull"], stream_output=True)

        print("Installing admin Python dependencies...")
        from pilot.managers.admin_environment import AdminEnvManager

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
            from pilot.managers.processes.local import ProcessManager

            manager = ProcessManager.detect_running(self.bench)
            if type(manager) is ProcessManager:
                return
            print("Restarting bench processes...")
            manager.restart()
        except Exception:
            pass
