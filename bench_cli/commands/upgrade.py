from __future__ import annotations

from bench_cli.utils import run_command
from bench_cli.commands.admin import download_admin_frontend, _cli_root


class UpgradeCommand:
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
        try:
            from bench_cli.core.bench import Bench
            from bench_cli.managers.supervisor_process_manager import SupervisorProcessManager
            from bench_cli.managers.process_manager import ProcessManagerFactory
            bench = Bench.for_directory()
            manager = ProcessManagerFactory.create(bench)
            if not isinstance(manager, SupervisorProcessManager):
                return
            if not manager.supervisor_conf_path.exists():
                return
            if not manager._is_supervisord_alive():
                return
            print("Restarting bench processes...")
            manager.restart()
        except Exception:
            pass
