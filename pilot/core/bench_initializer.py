from __future__ import annotations

import shutil
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pilot.core.bench import Bench

_BENCH_DIRS = ("apps", "sites", "logs", "config", "pids", "env", "admin", "tasks")


class BenchInitializer:
    """Initializes a freshly-created bench: system packages, directory
    structure, virtualenv, framework app, Node.js, Redis, admin frontend,
    and process config. Rolls back partial changes on failure."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench
        self._rollback_actions: list[tuple[str, Callable[[], None]]] = []

    def run(self, on_progress: Callable[[str], None] = lambda message: None) -> None:
        try:
            self._run_steps(on_progress)
        except Exception as exc:
            on_progress(f"\nError: {exc}")
            self._rollback(on_progress)
            raise

    def _run_steps(self, on_progress: Callable[[str], None]) -> None:
        from pilot.managers.python_environment import PythonEnvManager

        python_env_manager = PythonEnvManager(self.bench)

        # Production deployment (process manager, nginx, TLS) is intentionally
        # NOT done here — it's a separate `bench setup production` step. bench
        # init never needs root: system packages/services are installed once
        # by install.sh, and MariaDB/PostgreSQL run as a rootless per-user
        # server (see MariaDBManager/PostgresManager).
        steps: list[tuple[str, Callable[[], None]]] = [
            ("Validate bench.toml", self.bench.config.validate),
            ("Ensure admin password", self._ensure_admin_password),
            ("Install system packages", self._install_system_packages),
            ("Create bench directory structure", self._create_bench_structure),
            ("Create Python virtualenv", lambda: self._create_virtualenv(python_env_manager)),
            ("Clone and install framework app", lambda: self._install_framework_apps(python_env_manager, on_progress)),
            ("Install Node.js", python_env_manager.install_node),
            ("Install Node.js dependencies", python_env_manager.install_node_dependencies),
            ("Configure Redis", self._configure_redis),
            ("Download admin frontend", lambda: self._download_admin_frontend(on_progress)),
            ("Generate process config", self._generate_process_config),
        ]

        total = len(steps)
        for i, (description, action) in enumerate(steps, start=1):
            on_progress(f"[{i}/{total}] {description}...")
            action()

        on_progress("\nBench initialised. Next steps:")
        on_progress("  bench new-site site1.example.com   # create your first site")
        on_progress("  bench start                        # start all processes")

    def _rollback(self, on_progress: Callable[[str], None]) -> None:
        if not self._rollback_actions:
            return
        on_progress("\nRolling back changes...")
        for label, fn in reversed(self._rollback_actions):
            on_progress(f"  Removing {label}...")
            try:
                fn()
            except Exception as e:
                on_progress(f"    Warning: rollback step failed — {e}")
        on_progress("\nRollback complete. bench.toml is preserved — fix the issue and run init again.")

    def _remove_bench_dirs(self) -> None:
        for name in _BENCH_DIRS:
            p = self.bench.path / name
            if p.exists() or p.is_symlink():
                shutil.rmtree(p, ignore_errors=True)

    def _create_bench_structure(self) -> None:
        self.bench.create_directories()
        self.bench.write_common_site_config()
        self._rollback_actions.append(("bench directories", self._remove_bench_dirs))

    def _create_virtualenv(self, python_env_manager) -> None:
        python_env_manager.ensure_python()
        python_env_manager.create_venv()

    def _install_framework_apps(self, python_env_manager, on_progress: Callable[[str], None]) -> None:
        for app in self.bench.init_apps():
            if not app.is_cloned:
                on_progress(f"  Cloning {app.config.name}...")
                app.clone()
            on_progress(f"  Installing {app.config.name}...")
            python_env_manager.install_app(app)
        self.bench.write_apps_txt()

    def _ensure_admin_password(self) -> None:
        import secrets

        from pilot.config.toml_store import BenchTomlStore

        admin = self.bench.config.admin
        if not admin.enabled or admin.password:
            return
        admin.password = secrets.token_hex(nbytes=5)
        BenchTomlStore.for_bench(self.bench.path).write(self.bench.config)

    def _configure_redis(self) -> None:
        from pilot.managers.redis import RedisManager

        RedisManager(self.bench.config.redis, self.bench).generate_configs()

    def _generate_process_config(self) -> None:
        from pilot.managers.processes.local import ProcessManager

        ProcessManager.for_bench(self.bench).write_config()

    def _download_admin_frontend(self, on_progress: Callable[[str], None]) -> None:
        from pilot.core.admin_frontend import build_admin_frontend, download_admin_frontend
        from pilot.loader import cli_root

        if not download_admin_frontend(cli_root()):
            on_progress("  Pre-built download failed — building from source (requires Node.js)...")
            build_admin_frontend(on_progress=on_progress)

    def _install_system_packages(self) -> None:
        from pilot.managers.python_environment import PythonEnvManager
        from pilot.managers.redis import RedisManager
        from pilot.managers.packages import get_package_manager

        pkg = get_package_manager()

        # A bench runs exactly one engine; install/provision (or verify, if existing) only that one.
        if self.bench.config.db_type == "postgres":
            self._provision_or_verify(self._postgres_manager(), "PostgreSQL")
        elif self.bench.config.db_type == "sqlite":
            pass
        else:
            self._provision_or_verify(self._mariadb_manager(), "MariaDB")

        RedisManager(self.bench.config.redis, self.bench).install()
        self._install_build_headers(pkg)
        PythonEnvManager(self.bench).ensure_python()

    def _provision_or_verify(self, manager, label: str) -> None:
        if not manager.config.existing:
            manager.provision()
            return
        if not manager.check_credentials():
            from pilot.exceptions import BenchError

            raise BenchError(
                f"Could not connect to existing {label} server at "
                f"{manager.config.host}:{manager.config.port} as '{manager.config.admin_user}'. "
                "Check the host, port, username, and password."
            )

    def _install_build_headers(self, pkg) -> None:
        # frappe imports mysqlclient in its __init__.py for every engine, so the
        # MariaDB client headers are always required; postgres benches additionally
        # need libpq headers for psycopg. install.sh provisions them once for the
        # whole host, so bench init only ever verifies them.
        from pilot.exceptions import BenchError
        from pilot.managers.platform import is_linux

        if not is_linux():
            return
        postgres = self.bench.config.db_type == "postgres"
        packages = ["build-essential", "pkg-config", "git", "python3-dev", "libmariadb-dev"]
        if postgres:
            packages.append("libpq-dev")
        missing = [p for p in packages if not pkg.is_installed(p)]
        if missing:
            raise BenchError(
                f"Missing system packages: {', '.join(missing)}. Re-run install.sh "
                "as root to install them, or install them yourself."
            )

    def _postgres_manager(self):
        from pilot.managers.postgres import PostgresManager

        return PostgresManager(self.bench.config.postgres)

    def _mariadb_manager(self):
        from pilot.managers.mariadb import MariaDBManager

        return MariaDBManager(self.bench.config.mariadb)
