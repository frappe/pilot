from __future__ import annotations

import logging
import os
import subprocess
import time
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

    def wait_for_database(self, timeout_seconds: float = 60) -> None:
        """Wait until the production database accepts connections; raise on timeout."""
        from pilot.managers.database import MariaDBManager, PostgresManager

        config = self.bench.config
        if not config.production.enabled or config.db_type == "sqlite":
            return
        manager = (
            PostgresManager(config.postgres)
            if config.db_type == "postgres"
            else MariaDBManager(config.mariadb)
        )
        if manager.config.existing:
            return
        deadline = time.monotonic() + timeout_seconds
        while not manager.is_reachable():
            if time.monotonic() >= deadline:
                raise BenchError(
                    "The database did not become ready. Check its service and restart the workload."
                )
            time.sleep(0.1)

    @property
    def is_database_ready(self) -> bool:
        """Whether the database that Pilot runs for this production bench is reachable.
        True for an existing server, SQLite or a development bench."""
        from pilot.managers.database import MariaDBManager, PostgresManager

        config = self.bench.config
        if not config.production.enabled or config.db_type == "sqlite":
            return True
        manager = PostgresManager(config.postgres) if config.db_type == "postgres" else MariaDBManager(config.mariadb)
        return manager.config.existing or manager.is_reachable()

    def start(self, on_progress: Callable[[str], None]) -> None:
        """Start the bench in development, production or setup mode."""
        self.bench.site_aliases.release_expired()
        initialized = self.bench.is_initialized
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
        """Stop the bench processes and the Admin."""
        from pilot.managers.processes.local import ProcessManager

        manager = ProcessManager.detect_running(self.bench)
        manager.stop()
        manager.stop_admin()
        on_progress(f"Stopped bench {self.bench.config.name}.")

    def restart_workload(self, include_admin: bool, on_progress: Callable[[str], None]) -> None:
        """Restart production processes. Fails when there is nothing to restart."""
        if not self.bench.config.production.enabled:
            raise BenchError(_DEV_RESTART_MESSAGE)

        from pilot.managers.processes.local import ProcessManager

        manager = cast("ManagedProcessManager", ProcessManager.for_bench(self.bench))
        if not manager.is_configured():
            raise BenchError(incomplete_production_message(self.bench))

        manager.write_config()
        # Links app units added since the last setup, and unlinks removed ones.
        manager.install_config()
        manager.reload_manager_config()
        manager.restart()
        self.bench.site_aliases.release_expired()
        if include_admin:
            manager.restart_admin()

    def run_production_action(self, action: str) -> None:
        """Start, stop or restart the production processes."""
        from pilot.managers.processes.local import ProcessManager

        if not self.bench.config.production.enabled:
            raise BenchError("Start, stop, and restart are only supported for production benches.")
        manager = ProcessManager.for_bench(self.bench)
        operation = manager.start_workload if action == "start" else getattr(manager, action)
        operation()

    def rebuild_config(self) -> None:
        """Regenerate the Redis, process manager and nginx configs."""
        from pilot.managers.nginx import NginxManager
        from pilot.managers.processes.local import ProcessManager
        from pilot.managers.redis import RedisManager

        RedisManager(self.bench.config.redis, self.bench).generate_configs()
        ProcessManager.for_bench(self.bench).write_config()
        self.bench.write_common_site_config()
        if self.bench.config.production.enabled:
            NginxManager(self.bench).write_config()

    def rebuild_assets(self, apps: list[str] | None = None, force: bool = False) -> None:
        """Build the assets for some or all apps."""
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
        """Install every app's Python and node requirements."""
        self._install_python_requirements(on_progress)
        self._install_js_requirements(on_progress)

    def _start_development(self, initialized: bool, on_progress: Callable[[str], None]) -> None:
        """Start the development processes."""
        from pilot.managers.processes.local import ProcessManager

        try:
            ProcessManager(self.bench).stop()
        except Exception as exc:
            logging.debug("Best-effort stop of a stale process manager failed: %s", exc)
        if not initialized:
            self._start_wizard(on_progress)
            return
        self._warn_fixed_dev_site(on_progress)
        self._warn_missing_python_reloader(on_progress)
        manager = ProcessManager(self.bench)
        self._rebuild_manager_config(manager)
        if self.bench.config.admin.enabled and not manager.watch_admin_js:
            self._ensure_admin_dist(on_progress)
        manager.start()

    def _warn_missing_python_reloader(self, on_progress: Callable[[str], None]) -> None:
        """Warn when Python auto-reload is on but watchdog is missing."""
        bench = self.bench
        if bench.is_lite_mode and bench.config.reload_python and not bench.has_python_reloader:
            on_progress(
                "Python auto-reload is off: watchdog is not installed. "
                f"Install it with: uv pip install --python {bench.env_path / 'bin' / 'python'} watchdog"
            )

    def _warn_fixed_dev_site(self, on_progress: Callable[[str], None]) -> None:
        """Warn when a fixed default site is set in development."""
        from pilot.config.common_site_config import read_common_site_config

        if self.bench.is_lite_mode:
            return
        config = read_common_site_config(self.bench.sites_path)
        fixed_site = os.environ.get("FRAPPE_SITE") or config.get("default_site")
        if fixed_site and len(self.bench.sites()) > 1:
            on_progress(
                f"Warning: the development web server is pinned to '{fixed_site}'. "
                "Unset FRAPPE_SITE and remove default_site from common_site_config.json "
                "for Host-based routing across sites."
            )

    def _production_manager(self, process_manager: str) -> "ManagedProcessManager":
        """The production process manager by name."""
        if process_manager == "systemd":
            from pilot.managers.processes.systemd import SystemdProcessManager

            return SystemdProcessManager(self.bench)
        from pilot.managers.processes.supervisor import SupervisorProcessManager

        return SupervisorProcessManager(self.bench)

    def _start_setup_admin(self, manager: "ManagedProcessManager", on_progress) -> None:

        """Start only the Admin so the user can finish setup."""
        manager.start_admin()
        on_progress(f"Admin running at {self.bench.config.admin_url}")
        on_progress("Finish setup there; the bench starts serving once it's initialized.")

    def _rebuild_manager_config(self, manager: "ProcessManager") -> None:
        """Write the process manager config and the common site config."""
        manager.write_config()
        self.bench.write_common_site_config()

    def _ensure_admin_dist(self, on_progress: Callable[[str], None]) -> None:
        """Build the Admin frontend if it is missing."""
        from admin.backend.frontend import is_admin_frontend_built
        from pilot import is_dev_build
        from pilot.core.server import Server

        root = Server().root
        frontend = root / "admin" / "frontend" / "dashboard"
        # Releases ship the source too; only dev builds may (re)compile it.
        can_build = is_dev_build and (frontend / "package.json").exists()

        if not is_admin_frontend_built(root) and not can_build:
            raise BenchError(
                "Admin UI is missing from this release. Reinstall Pilot, or run it from a source checkout."
            )
        if can_build:
            self._rebuild_admin(on_progress)

    def _rebuild_admin(self, on_progress: Callable[[str], None]) -> None:
        """Build the Admin frontend; report a failure."""
        from admin.backend.frontend import build_admin_frontend, is_admin_frontend_built
        from pilot.core.server import Server

        try:
            build_admin_frontend(on_progress=on_progress)
        except BenchError as error:
            if not is_admin_frontend_built(Server().root):
                raise
            on_progress(f"  Could not rebuild the admin UI ({error}); serving the existing build.")

    def _start_wizard(self, on_progress: Callable[[str], None]) -> None:
        """Start the Admin setup wizard."""
        from admin.backend.frontend import ensure_admin_frontend, is_admin_frontend_built
        from pilot.core.server import Server
        from pilot.managers.environment import AdminEnvManager

        root = Server().root
        admin_mgr = AdminEnvManager(root)
        admin_mgr.ensure()

        if not is_admin_frontend_built(root):
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

        if self.bench.is_initialized:
            on_progress("\nSetup complete. Run 'pilot start' to start your bench.\n")

    def _admin_port(self) -> int:
        """The Admin port from bench.toml."""
        import tomllib

        from pilot.config import BenchConfig

        try:
            return BenchConfig.read(self.bench.path, validate=False).admin.port
        except (OSError, tomllib.TOMLDecodeError):
            return 7000

    def _install_python_requirements(self, on_progress: Callable[[str], None]) -> None:
        """Install the Python package of every app."""
        from pilot.managers.environment import PythonEnvManager

        for app in self.bench.apps():
            if not (app.path / "pyproject.toml").exists() and not (app.path / "setup.py").exists():
                continue
            on_progress(f"Installing Python requirements for {app.config.name}...")
            PythonEnvManager(self.bench).install_app(app)

    def _install_js_requirements(self, on_progress: Callable[[str], None]) -> None:
        """Install the node packages of every app that has them."""
        from pilot.core.app.node_packages import NodePackages

        for app in self.bench.apps():
            packages = NodePackages(app.path)
            if packages.has_package_json:
                on_progress(f"Installing JS requirements for {app.config.name}...")
                packages.install(frozen=False)


def incomplete_production_message(bench: "Bench") -> str:
    """The message for a production bench whose deployment is incomplete."""
    pm = bench.config.production.process_manager
    return (
        f"Bench {bench.config.name} is configured for production, but its {pm}\n"
        f"deployment is incomplete.\n\n"
        f"Repair it with:\n"
        f"  pilot -b {bench.config.name} setup production"
    )
