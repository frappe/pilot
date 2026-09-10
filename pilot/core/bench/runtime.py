from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.core.bench import Bench
    from pilot.managers.processes.base import ManagedProcessManager
    from pilot.managers.processes.local import ProcessManager


_DEV_RESTART_MESSAGE = (
    "Restart is available only for production benches managed by\n"
    "systemd or Supervisor.\n\n"
    "For development, stop the runner and execute `pilot start` again."
)


class BenchRuntime:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def start(self, on_progress: Callable[[str], None]) -> None:
        initialized = self._is_initialized()
        process_manager = self.bench.config.production.process_manager

        if not process_manager:
            self._start_development(initialized, on_progress)
            return

        manager = self._production_manager(process_manager)
        if not initialized:
            self._start_setup_admin(manager, on_progress)
            return

        self._rebuild_manager_config(manager)
        if not manager.is_configured():
            on_progress(incomplete_production_message(self.bench))
            return
        manager.start()

    def stop(self, on_progress: Callable[[str], None]) -> None:
        from pilot.managers.processes.local import ProcessManager

        manager = ProcessManager.detect_running(self.bench)
        manager.stop()
        manager.stop_admin()
        on_progress(f"Stopped bench {self.bench.config.name}.")

    def restart_workload(self, include_admin: bool, on_progress: Callable[[str], None]) -> None:
        if not self.bench.config.production.enabled:
            on_progress(_DEV_RESTART_MESSAGE)
            return

        from pilot.managers.processes.local import ProcessManager

        manager = cast("ManagedProcessManager", ProcessManager.for_bench(self.bench))
        if not manager.is_configured():
            on_progress(incomplete_production_message(self.bench))
            return

        manager.write_config()
        manager.reload_manager_config()
        manager.restart()
        if include_admin:
            manager.restart_admin()

    def run_production_action(self, action: str) -> None:
        from pilot.managers.processes.local import ProcessManager

        if not self.bench.config.production.enabled:
            raise BenchError("Start, stop, and restart are only supported for production benches.")
        manager = ProcessManager.for_bench(self.bench)
        operation = manager.start_workload if action == "start" else getattr(manager, action)
        operation()

    def rebuild_config(self) -> None:
        from pilot.managers.nginx import NginxManager
        from pilot.managers.processes.local import ProcessManager
        from pilot.managers.redis import RedisManager

        RedisManager(self.bench.config.redis, self.bench).generate_configs()
        ProcessManager.for_bench(self.bench).write_config()
        self.bench.write_common_site_config()
        if self.bench.config.production.enabled:
            NginxManager(self.bench).generate_config()

    def rebuild_assets(self, apps: list[str] | None = None, force: bool = False) -> None:
        from pilot.managers.environment import PythonEnvManager
        from pilot.managers.processes.local import ProcessManager

        manager = PythonEnvManager(self.bench)
        if force and not apps:
            manager.build_assets()
        else:
            for app in self.apps_to_build(apps):
                manager.build_assets_for_app(app, force=force)
        ProcessManager.for_bench(self.bench).reload_workers(web_only=True)

    def apps_to_build(self, names: list[str] | None) -> list["App"]:
        """Bench apps matching `names`; every app when no names are given."""
        apps = self.bench.apps()
        if not names:
            return apps
        by_name = {app.config.name: app for app in apps}
        missing = [name for name in names if name not in by_name]
        if missing:
            raise BenchError(f"App(s) not found in bench: {', '.join(missing)}")
        return [by_name[name] for name in names]

    def install_requirements(self, on_progress: Callable[[str], None]) -> None:
        self._install_python_requirements(on_progress)
        self._install_js_requirements(on_progress)

    def _start_development(self, initialized: bool, on_progress: Callable[[str], None]) -> None:
        from pilot.managers.processes.local import ProcessManager

        try:
            ProcessManager(self.bench).stop()
        except Exception as exc:
            logging.debug("Best-effort stop of a stale process manager failed: %s", exc)
        if not initialized:
            self._start_wizard(on_progress)
            return
        manager = ProcessManager(self.bench)
        self._rebuild_manager_config(manager)
        if not manager.watch_admin_js:
            self._ensure_admin_dist(on_progress)
        manager.start()

    def _production_manager(self, process_manager: str) -> "ManagedProcessManager":
        if process_manager == "systemd":
            from pilot.managers.processes.systemd import SystemdProcessManager

            return SystemdProcessManager(self.bench)
        from pilot.managers.processes.supervisor import SupervisorProcessManager

        return SupervisorProcessManager(self.bench)

    def _start_setup_admin(self, manager: "ManagedProcessManager", on_progress) -> None:
        from pilot.utils import admin_url

        manager.start_admin()
        on_progress(f"Admin running at {admin_url(self.bench.config)}")
        on_progress("Finish setup there; the bench starts serving once it's initialized.")

    def _rebuild_manager_config(self, manager: "ProcessManager") -> None:
        manager.write_config()
        self.bench.write_common_site_config()

    def _ensure_admin_dist(self, on_progress: Callable[[str], None]) -> None:
        from pilot import is_dev_build
        from pilot.utils import cli_root

        root = cli_root()
        dist = root / "admin" / "backend" / "static" / "dashboard"
        frontend = root / "admin" / "frontend" / "dashboard"
        # Releases ship the source too; only dev builds may (re)compile it.
        can_build = is_dev_build and (frontend / "package.json").exists()

        if not (dist / "assets").exists() and not can_build:
            raise BenchError(
                "Admin UI is missing from this release. Reinstall Pilot, "
                "or run it from a source checkout."
            )
        if can_build:
            self._rebuild_admin(on_progress)

    def _rebuild_admin(self, on_progress: Callable[[str], None]) -> None:
        from admin.backend.frontend import build_admin_frontend

        try:
            build_admin_frontend(on_progress=on_progress)
        except BenchError as error:
            on_progress(f"  Could not rebuild the admin UI ({error}); serving the existing build.")

    def _start_wizard(self, on_progress: Callable[[str], None]) -> None:
        from admin.backend.frontend import ensure_admin_frontend
        from pilot.managers.environment import AdminEnvManager
        from pilot.utils import cli_root

        root = cli_root()
        admin_mgr = AdminEnvManager(root)
        admin_mgr.ensure()

        assets = root / "admin" / "backend" / "static" / "dashboard" / "assets"
        if not assets.exists():
            ensure_admin_frontend(on_progress)

        port = self._admin_port()
        on_progress("\nBench not initialized. Starting setup wizard...")
        on_progress(f"  Open http://localhost:{port}/?sid={self.bench.issue_setup_link()} in your browser")
        on_progress("  The link signs you in and expires in an hour.\n")

        env = {**os.environ, "PYTHONPATH": str(root)}
        subprocess.run(
            [
                str(admin_mgr.python),
                "-m",
                "admin.backend.run_server",
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

        if self._is_initialized():
            on_progress("\nSetup complete. Run 'pilot start' to start your bench.\n")

    def _admin_port(self) -> int:
        import tomllib

        from pilot.config import BenchConfig

        try:
            return BenchConfig.read(self.bench.path, validate=False).admin.port
        except (OSError, tomllib.TOMLDecodeError):
            return 7000

    def _is_initialized(self) -> bool:
        return self.bench.python.exists()

    def _install_python_requirements(self, on_progress: Callable[[str], None]) -> None:
        from pilot.managers.environment import ensure_uv
        from pilot.utils import run_command

        uv = ensure_uv()
        python = str(self.bench.python)

        for app in self.bench.apps():
            if not (app.path / "pyproject.toml").exists() and not (app.path / "setup.py").exists():
                continue
            on_progress(f"Installing Python requirements for {app.config.name}...")
            run_command(
                [uv, "pip", "install", "--python", python, "-e", app.editable_target],
                stream_output=True,
            )

    def _install_js_requirements(self, on_progress: Callable[[str], None]) -> None:
        from pilot.utils import get_yarn_bin, run_command

        for app in self.bench.apps():
            if not (app.path / "package.json").exists():
                continue
            on_progress(f"Installing JS requirements for {app.config.name}...")
            run_command([get_yarn_bin(), "install"], cwd=app.path, stream_output=True)


def incomplete_production_message(bench: "Bench") -> str:
    pm = bench.config.production.process_manager
    return (
        f"Bench {bench.config.name} is configured for production, but its {pm}\n"
        f"deployment is incomplete.\n\n"
        f"Repair it with:\n"
        f"  pilot -b {bench.config.name} setup production"
    )
