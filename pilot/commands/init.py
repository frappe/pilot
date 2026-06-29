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
        from pilot.managers.python_env_manager import PythonEnvManager
        from pilot.platform import is_linux

        self._check_passwordless_sudo()

        volume_enabled = is_linux() and self.bench.config.volume.enabled
        # A dedicated MariaDB instance is only provisioned for MariaDB benches;
        # PostgreSQL benches run against the shared server (no per-bench instance).
        dedicated_db = is_linux() and self.bench.config.db_type == "mariadb" and bool(self.bench.config.mariadb.instance)
        # Passwordless sudo is set up by install.sh and enforced above by
        # _check_passwordless_sudo, so the steps below never block on a prompt.
        python_env_manager = PythonEnvManager(self.bench)

        # The ordered list of steps that will actually run, so the progress total
        # is derived from the steps themselves rather than a hand-counted number
        # that drifts whenever a step is added or removed. Production deployment
        # (process manager, nginx, TLS) is intentionally NOT done here — it's a
        # separate `bench setup production` step, run by the wizard when the user
        # opts in and available standalone from the CLI.
        steps: list[tuple[str, Callable[[], None]]] = [
            ("Validate bench.toml", self.bench.config.validate),
            ("Ensure admin password", self._ensure_admin_password),
            ("Install system packages", self._install_system_packages),
        ]
        if volume_enabled:
            steps.append(("Set up ZFS volumes", self._setup_volume))
        if dedicated_db:
            steps.append(("Provision MariaDB instance", self._provision_mariadb_instance))
        steps += [
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

        from pilot.config.toml_writer import bench_config_to_toml

        admin = self.bench.config.admin
        if not admin.enabled or admin.password:
            return
        admin.password = secrets.token_hex(nbytes=5)
        (self.bench.path / "bench.toml").write_text(bench_config_to_toml(self.bench.config))

    def _configure_redis(self) -> None:
        from pilot.managers.redis_manager import RedisManager

        RedisManager(self.bench.config.redis, self.bench).generate_configs()

    def _generate_process_config(self) -> None:
        from pilot.managers.process_manager import ProcessManagerFactory

        ProcessManagerFactory.create(self.bench).generate_config()

    def _check_passwordless_sudo(self) -> None:
        from pilot.platform import has_passwordless_sudo, is_linux

        if not is_linux() or has_passwordless_sudo():
            return
        raise RuntimeError(
            "Passwordless sudo is not configured for this user. bench init needs it to "
            "install packages and manage services without a password prompt.\n"
            "Set it up by re-running the installer:\n"
            "  curl -fsSL https://raw.githubusercontent.com/frappe/bench-cli/main/install.sh | bash\n"
            "or add /etc/sudoers.d/<user> containing: <user> ALL=(ALL) NOPASSWD: ALL"
        )

    def _step(self, description: str) -> None:
        self._step_counter += 1
        print(f"[{self._step_counter}/{self._total_steps}] {description}...", flush=True)

    def _download_admin_frontend(self) -> None:
        from pilot.commands.admin import BuildAdminCommand, _cli_root, download_admin_frontend

        if not download_admin_frontend(_cli_root()):
            print("  Pre-built download failed — building from source (requires Node.js)...")
            BuildAdminCommand().run()

    def _setup_volume(self) -> None:
        from pilot.commands.volume import VolumeSetupCommand

        VolumeSetupCommand(self.bench.config.volume, self.bench.path, bench_config=self.bench.config).run()

    def _provision_mariadb_instance(self) -> None:
        from pilot.managers.mariadb_manager import MariaDBManager

        # Runs after _setup_volume: if volume is enabled, the bench's mariadb
        # dataset is already mounted at the instance datadir, so install-db
        # writes straight onto ZFS; otherwise the datadir is a plain directory.
        MariaDBManager(self.bench.config.mariadb).provision_instance(self.bench.config_path)

    # Build/runtime deps for compiling frappe's Python and Node wheels on Alpine.
    # musl ships no manylinux wheels, so the full header set is needed; bash and
    # tzdata are runtime deps frappe assumes are present. python3-dev provides
    # Python.h: Alpine ships a system python that `uv venv` reuses, so C
    # extensions (mysqlclient, etc.) need the matching dev headers to compile.
    _ALPINE_BUILD_PACKAGES = (
        "build-base", "pkgconf", "git", "bash", "tzdata",
        "python3-dev", "linux-headers", "libffi-dev", "openssl-dev", "libxml2-dev",
        "libxslt-dev", "jpeg-dev", "zlib-dev", "freetype-dev", "tiff-dev",
        "lcms2-dev", "openjpeg-dev",
    )

    def _install_system_packages(self) -> None:
        from pilot.managers.python_env_manager import PythonEnvManager
        from pilot.managers.redis_manager import RedisManager
        from pilot.platform import get_package_manager, is_linux

        pkg = get_package_manager()
        if is_linux():
            pkg.update()

        # A bench runs exactly one engine; install/provision only that one.
        if self.bench.config.db_type == "postgres":
            self._postgres_manager().provision()
        else:
            self._install_mariadb()

        RedisManager(self.bench.config.redis, self.bench).install()
        self._install_build_headers(pkg)
        PythonEnvManager(self.bench).ensure_python()

    def _install_build_headers(self, pkg) -> None:
        # frappe imports mysqlclient in its __init__.py for every engine, so the
        # MariaDB client headers are always required; postgres benches additionally
        # need libpq headers for psycopg.
        from pilot.platform import is_alpine, is_linux

        postgres = self.bench.config.db_type == "postgres"
        if is_alpine():
            packages = [*self._ALPINE_BUILD_PACKAGES, "mariadb-dev"]
            if postgres:
                packages.append(self._postgres_manager().alpine_dev_package())
            pkg.install(*packages)
        elif is_linux():
            packages = ["build-essential", "pkg-config", "git", "python3-dev", "libmariadb-dev"]
            if postgres:
                packages.append("libpq-dev")
            pkg.install(*packages)

    def _postgres_manager(self):
        from pilot.managers.postgres_manager import PostgresManager

        return PostgresManager(self.bench.config.postgres)

    def _install_mariadb(self) -> None:
        from pilot.managers.mariadb_manager import MariaDBManager
        from pilot.platform import is_linux

        mariadb_manager = MariaDBManager(self.bench.config.mariadb)
        freshly_installed = not mariadb_manager.is_installed()
        mariadb_manager.install()

        if mariadb_manager.is_dedicated:
            # Install the package only; the instance is provisioned after volume
            # setup (see _do_run) so a ZFS-backed datadir, if any, is mounted
            # before mariadb-install-db runs against it.
            if freshly_installed and is_linux():
                # apt auto-starts the shared mariadb service on port 3306; stop and
                # disable it so the dedicated instance can claim its port.
                mariadb_manager.stop_shared()
            return

        port_config_written = mariadb_manager.configure_shared_port()
        if port_config_written:
            # Config was written — restart to apply the new port (also starts a
            # stopped service).
            mariadb_manager.restart()
        else:
            mariadb_manager.start()
        if freshly_installed or mariadb_manager.is_unsecured():
            mariadb_manager.secure_installation()
        elif not mariadb_manager.check_credentials():
            raise RuntimeError(
                "MariaDB is already installed but the configured root password is incorrect. "
                "Fix mariadb.root_password in bench.toml (or secure the existing MariaDB) and retry."
            )
