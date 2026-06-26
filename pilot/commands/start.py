from __future__ import annotations

import argparse
import os
import subprocess
import tomllib
from typing import TYPE_CHECKING

from pilot.commands.base import Command

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class RunCommand(Command):
    name = "start"
    help = "Start all bench processes."
    supports_all_benches = True

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--admin-dev",
            action="store_true",
            help="Develop the admin UI: live-rebuild it and run the Vite dev server. "
                 "By default the admin backend just serves the prebuilt UI from dist.",
        )

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, admin_dev=args.admin_dev)

    def __init__(self, bench: "Bench", admin_dev: bool = False) -> None:
        self.bench = bench
        self.admin_dev = admin_dev

    def run(self) -> None:
        from pilot.managers.process_manager import ProcessManager

        initialized = (self.bench.path / "env" / "bin" / "python").exists()
        process_manager = self.bench.config.production.process_manager

        # Dev bench (no process manager): run in the foreground, or the
        # standalone setup wizard if it isn't initialized yet. Stop any existing
        # instance first (best-effort) so a stale process doesn't hold the ports.
        if not process_manager:
            try:
                ProcessManager(self.bench).stop()
            except Exception:
                pass
            if not initialized:
                self._start_wizard()
                return
            # In --admin-dev the Vite watcher rebuilds dist itself.
            if not self.admin_dev:
                self._ensure_admin_dist()
            ProcessManager(self.bench, admin_dev=self.admin_dev).start()
            return

        # Production bench (systemd/supervisor/openrc): the admin always runs
        # under the process manager. Pick by the configured manager rather than
        # via the factory, which gates on production.enabled.
        if process_manager == "systemd":
            from pilot.managers.systemd_process_manager import SystemdProcessManager

            manager = SystemdProcessManager(self.bench)
        elif process_manager == "openrc":
            from pilot.managers.openrc_process_manager import OpenRCProcessManager

            manager = OpenRCProcessManager(self.bench)
        else:
            from pilot.managers.supervisor_process_manager import SupervisorProcessManager

            manager = SupervisorProcessManager(self.bench)

        if not initialized:
            # No workload yet — bring up just the admin (socket-activated) so the
            # setup wizard is served at the bench's domain. The workload starts
            # once the bench is initialized and `setup production` is run.
            from pilot.admin_url import admin_url

            manager.setup_admin()
            print(f"Admin running at {admin_url(self.bench.config)}")
            print("Finish setup there; the bench starts serving once it's initialized.")
            return

        if not manager.is_configured():
            from pilot.commands.restart import _incomplete_message

            print(_incomplete_message(self.bench))
            return
        manager.start()

    def _ensure_admin_dist(self) -> None:
        # Build the admin UI from source if present, else download a prebuilt copy.
        from pilot.commands.admin import BuildAdminCommand, _cli_root, download_admin_frontend

        cli_root = _cli_root()
        if (cli_root / "admin" / "backend" / "static" / "dist" / "assets").exists():
            return
        print("Admin UI not built yet; building it now...")
        if (cli_root / "admin" / "frontend" / "package.json").exists():
            BuildAdminCommand(force_build=True).run()
        else:
            download_admin_frontend(cli_root)

    def _start_wizard(self) -> None:
        from pilot.commands.admin import download_admin_frontend, _cli_root
        from pilot.managers.admin_env_manager import AdminEnvManager

        cli_root = _cli_root()
        admin_mgr = AdminEnvManager(cli_root)
        admin_mgr.ensure()

        assets = cli_root / "admin" / "backend" / "static" / "dist" / "assets"
        if not assets.exists():
            print("Downloading admin frontend...")
            download_admin_frontend(cli_root)

        port = self._admin_port()
        print("\nBench not initialized. Starting setup wizard...")
        print(f"  Open http://localhost:{port} in your browser\n")

        env = {**os.environ, "PYTHONPATH": str(cli_root)}
        subprocess.run(
            [
                str(admin_mgr.python),
                "-m",
                "admin.backend.server",
                "--bench-root",
                str(self.bench.path),
                "--port",
                str(port),
                "--timeout",
                "7200",
                "--wizard",
            ],
            env=env,
        )

        if (self.bench.path / "env" / "bin" / "python").exists():
            print("\nSetup complete. Run 'bench start' to start your bench.\n", flush=True)

    def _admin_port(self) -> int:
        try:
            with open(self.bench.path / "bench.toml", "rb") as f:
                return tomllib.load(f).get("admin", {}).get("port", 7000)
        except Exception:
            return 7000
