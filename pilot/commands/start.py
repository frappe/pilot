from __future__ import annotations

import os
import subprocess
from typing import TYPE_CHECKING

from pilot.commands.base import Command

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class RunCommand(Command):
    name = "start"
    help = "Start all bench processes."
    supports_all_benches = True

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench)

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

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
            manager = ProcessManager(self.bench)
            self._rebuild_config(manager)
            # In admin dev mode the Vite watcher rebuilds dist itself.
            if not manager.watch_admin_js:
                self._ensure_admin_dist()
            manager.start()
            return

        # Production bench (systemd/supervisor/openrc): the admin always runs
        # under the process manager. Pick by the configured manager rather than
        # via the factory, which gates on production.enabled.
        if process_manager == "systemd":
            from pilot.managers.process_managers.systemd import SystemdProcessManager

            manager = SystemdProcessManager(self.bench)
        elif process_manager == "openrc":
            from pilot.managers.process_managers.openrc import OpenRCProcessManager

            manager = OpenRCProcessManager(self.bench)
        else:
            from pilot.managers.process_managers.supervisor import SupervisorProcessManager

            manager = SupervisorProcessManager(self.bench)

        if not initialized:
            # No workload yet — bring up just the admin (socket-activated) so the
            # setup wizard is served at the bench's domain. The workload starts
            # once the bench is initialized and `setup production` is run.
            from pilot.admin_url import admin_url

            manager.start_admin()
            print(f"Admin running at {admin_url(self.bench.config)}")
            print("Finish setup there; the bench starts serving once it's initialized.")
            return

        self._rebuild_config(manager)
        if not manager.is_configured():
            from pilot.commands.restart import _incomplete_message

            print(_incomplete_message(self.bench))
            return
        manager.start()

    def _rebuild_config(self, manager) -> None:
        manager.write_config()
        self.bench.write_common_site_config()

    def _ensure_admin_dist(self) -> None:
        # Serve the admin UI from dist. In a source checkout, (re)build from source
        # when dist is missing or the frontend source changed since the last build,
        # so `bench start` reflects local UI edits without a manual `build-admin`.
        # A non-source install (no admin/frontend) just downloads the prebuilt copy.
        from pilot.commands.admin import BuildAdminCommand, _cli_root, download_admin_frontend

        cli_root = _cli_root()
        dist = cli_root / "admin" / "backend" / "static" / "dist"
        frontend = cli_root / "admin" / "frontend"
        has_source = (frontend / "package.json").exists()

        if not (dist / "assets").exists():
            print("Admin UI not built yet; building it now...")
            if has_source:
                BuildAdminCommand(force_build=True).run()
            else:
                download_admin_frontend(cli_root)
            return

        if has_source and self._admin_source_is_newer(frontend, dist):
            self._rebuild_admin(BuildAdminCommand)

    @staticmethod
    def _rebuild_admin(build_command) -> None:
        from pilot.exceptions import BenchError

        print("Admin UI source changed since last build; rebuilding...")
        try:
            build_command(force_build=True).run()
        except BenchError as error:
            # Never block startup on a build failure (e.g. Node too old) — keep
            # serving the existing dist and surface why.
            print(f"  Could not rebuild the admin UI ({error}); serving the existing build.")

    @staticmethod
    def _admin_source_is_newer(frontend, dist) -> bool:
        """True when any frontend source file is newer than the built bundle."""
        built_at = (dist / "index.html").stat().st_mtime
        for name in ("src", "index.html", "package.json", "vite.config.js"):
            path = frontend / name
            if path.is_dir():
                if any(f.stat().st_mtime > built_at for f in path.rglob("*") if f.is_file()):
                    return True
            elif path.exists() and path.stat().st_mtime > built_at:
                return True
        return False

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
        from pilot.config.toml_store import BenchTomlStore

        try:
            return BenchTomlStore.for_bench(self.bench.path).read_raw().get("admin", {}).get("port", 7000)
        except Exception:
            return 7000
