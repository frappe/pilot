from __future__ import annotations

import shutil
from collections.abc import Callable
from typing import TYPE_CHECKING

from pilot.commands.base import Command

if TYPE_CHECKING:
    from pilot.core.bench import Bench

_BENCH_DIRS = ("apps", "sites", "logs", "config", "pids", "env", "admin", "tasks")


class InitCommand(Command):
    name = "init"
    help = "Initialise the bench."
    # Heavy/irreversible — never guess the target bench.
    requires_explicit_bench = True

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench
        self._step_counter = 0
        self._total_steps = 0
        self._rollback_actions: list[tuple[str, Callable[[], None]]] = []

    def run(self) -> None:
        try:
            self._do_run()
        except Exception as exc:
            print(f"\nError: {exc}", flush=True)
            self._rollback()
            raise

    # ── rollback infrastructure ────────────────────────────────────────────

    def _on_rollback(self, label: str, fn: Callable[[], None]) -> None:
        self._rollback_actions.append((label, fn))

    def _rollback(self) -> None:
        if not self._rollback_actions:
            return
        print("\nRolling back changes...", flush=True)
        for label, fn in reversed(self._rollback_actions):
            print(f"  Removing {label}...", flush=True)
            try:
                fn()
            except Exception as e:
                print(f"    Warning: rollback step failed — {e}", flush=True)
        print(
            "\nRollback complete. bench.toml is preserved — fix the issue and run init again.",
            flush=True,
        )

    def _remove_bench_dirs(self) -> None:
        for name in _BENCH_DIRS:
            p = self.bench.path / name
            if p.exists() or p.is_symlink():
                shutil.rmtree(p, ignore_errors=True)

    # ── init steps ─────────────────────────────────────────────────────────

    def _do_run(self) -> None:
        from pilot.managers.python_environment import PythonEnvManager

        python_env_manager = PythonEnvManager(self.bench)

        # The ordered list of steps that will actually run, so the progress total
        # is derived from the steps themselves rather than a hand-counted number
        # that drifts whenever a step is added or removed. Production deployment
        # (process manager, nginx, TLS) is intentionally NOT done here — it's a
        # separate `bench setup production` step, run by the wizard when the user
        # opts in and available standalone from the CLI. bench init never needs
        # root: system packages/services are installed once by install.sh, and
        # MariaDB/PostgreSQL run as a rootless per-user server (see
        # MariaDBManager/PostgresManager).
        steps: list[tuple[str, Callable[[], None]]] = [
            ("Validate bench.toml", self.bench.config.validate),
            ("Ensure admin password", self._ensure_admin_password),
            ("Install system packages", self._install_system_packages),
            ("Create bench directory structure", self._create_bench_structure),
            ("Create Python virtualenv", lambda: self._create_virtualenv(python_env_manager)),
            ("Clone and install framework app", lambda: self._install_framework_apps(python_env_manager)),
            ("Install Node.js", python_env_manager.install_node),
            ("Install Node.js dependencies", python_env_manager.install_node_dependencies),
            ("Configure Redis", self._configure_redis),
            ("Download admin frontend", self._download_admin_frontend),
            ("Generate process config", self._generate_process_config),
        ]

        self._total_steps = len(steps)
        for description, action in steps:
            self._step(description)
            action()

        print("\nBench initialised. Next steps:")
        print("  bench new-site site1.example.com   # create your first site")
        print("  bench start                        # start all processes")

    def _create_bench_structure(self) -> None:
        self.bench.create_directories()
        self.bench.write_common_site_config()
        self._on_rollback("bench directories", self._remove_bench_dirs)

    def _create_virtualenv(self, python_env_manager) -> None:
        python_env_manager.ensure_python()
        python_env_manager.create_venv()

    def _install_framework_apps(self, python_env_manager) -> None:
        for app in self.bench.init_apps():
            if not app.is_cloned:
                print(f"  Cloning {app.config.name}...")
                app.clone()
            print(f"  Installing {app.config.name}...")
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

    def _step(self, description: str) -> None:
        self._step_counter += 1
        print(f"[{self._step_counter}/{self._total_steps}] {description}...", flush=True)

    def _download_admin_frontend(self) -> None:
        from pilot.commands.admin.start import BuildAdminCommand, download_admin_frontend
        from pilot.loader import cli_root

        if not download_admin_frontend(cli_root()):
            print("  Pre-built download failed — building from source (requires Node.js)...")
            BuildAdminCommand().run()

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
