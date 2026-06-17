from __future__ import annotations

from typing import TYPE_CHECKING

from bench_cli.commands.base import Command

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class UpgradeCommand(Command):
    name = "upgrade"
    help = "Pull latest bench-cli and download the admin frontend."
    requires_bench = False

    @classmethod
    def from_args(cls, args, bench):
        # Bench is optional: used only to restart processes in production.
        if bench is None:
            from bench_cli.loader import load_bench

            try:
                bench = load_bench()
            except Exception:
                bench = None
        return cls(bench)

    def __init__(self, bench: "Bench | None" = None) -> None:
        self.bench = bench

    def run(self) -> None:
        from bench_cli.commands.admin import download_admin_frontend, _cli_root
        from bench_cli.utils import run_command

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
