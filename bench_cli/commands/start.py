from __future__ import annotations

import os
import subprocess
import tomllib
from typing import TYPE_CHECKING

from bench_cli.commands.base import Command

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class RunCommand(Command):
    name = "start"
    help = "Start all bench processes."

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def run(self) -> None:
        from bench_cli.managers.process_manager import ProcessManagerFactory

        if not (self.bench.path / "env" / "bin" / "python").exists():
            self._start_wizard()
            return

        manager = ProcessManagerFactory.create(self.bench)
        if self.bench.config.production.enabled and not manager.is_configured():
            from bench_cli.commands.restart import _incomplete_message

            print(_incomplete_message(self.bench))
            return
        manager.start()

    def _start_wizard(self) -> None:
        from bench_cli.commands.admin import download_admin_frontend, _cli_root
        from bench_cli.managers.admin_env_manager import AdminEnvManager

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
