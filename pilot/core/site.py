from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config.site import SiteConfig
from pilot.exceptions import BenchError
from pilot.utils import run_command

if TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.core.bench import Bench


class Site:
    def __init__(self, config: SiteConfig, bench: "Bench") -> None:
        self.config = config
        self.bench = bench

    @property
    def path(self) -> Path:
        return self.bench.sites_path / self.config.name

    @property
    def exists(self) -> bool:
        return (self.path / "site_config.json").exists()

    def _frappe_call(self, *args: str) -> list[str]:
        """Build a frappe bench_helper command."""
        return [*self.bench.frappe_call, *args]

    def create(self, db_type: str | None = None) -> None:
        if not isinstance(self.config.admin_password, str) or not self.config.admin_password.strip():
            raise BenchError("Site Administrator password must not be empty.")
        cmd = self._frappe_call("frappe", "--site", self.config.name, "new-site", self.config.name)
        cmd += ["--admin-password", self.config.admin_password]
        effective = db_type or self.bench.config.db_type
        if effective == "postgres":
            cmd += self._postgres_db_args()
        elif effective == "sqlite":
            cmd += self._sqlite_db_args()
        else:
            from pilot.managers.mariadb import MariaDBManager

            socket_path = MariaDBManager(self.bench.config.mariadb)._detect_socket()
            cmd += self._mariadb_db_args(socket_path)
        run_command(cmd, cwd=self.bench.sites_path, stream_output=True)

    def _mariadb_db_args(self, socket_path: str) -> list[str]:
        mariadb = self.bench.config.mariadb
        args = ["--db-root-username", mariadb.admin_user]
        if socket_path:
            args += ["--db-socket", socket_path]
            # unix_socket auth ignores the password; pass a non-empty placeholder
            # so frappe doesn't fall back to an interactive getpass() prompt
            args += ["--db-root-password", mariadb.root_password or "socket_auth"]
        else:
            args += ["--db-host", mariadb.host, "--db-port", str(mariadb.port)]
            if mariadb.root_password:
                args += ["--db-root-password", mariadb.root_password]
        return args

    def _postgres_db_args(self) -> list[str]:
        postgres = self.bench.config.postgres
        return [
            "--db-type", "postgres",
            "--db-host", postgres.host,
            "--db-port", str(postgres.port),
            "--db-root-username", postgres.admin_user,
            "--db-root-password", self.bench.postgres_root_password(),
        ]

    def _sqlite_db_args(self) -> list[str]:
        return ["--db-type", "sqlite"]

    def restore(self, db_file: str, public_files: str | None = None, private_files: str | None = None) -> None:
        cmd = self._frappe_call("frappe", "--site", self.config.name, "restore", db_file)
        if public_files:
            cmd += ["--with-public-files", public_files]
        if private_files:
            cmd += ["--with-private-files", private_files]
        # restore reads the engine from the site's config (frappe.init); it only
        # needs the matching root credentials, not a --db-type flag.
        cmd += self.bench.db_root_args()
        run_command(cmd, cwd=self.bench.sites_path, stream_output=True)

    def reinstall(self, admin_password: str) -> None:
        if not isinstance(admin_password, str) or not admin_password.strip():
            raise BenchError("Site Administrator password must not be empty.")
        cmd = self._frappe_call("frappe", "--site", self.config.name, "reinstall", "--yes", "--admin-password", admin_password)
        cmd += self.bench.db_root_args()
        run_command(cmd, cwd=self.bench.sites_path, stream_output=True)

    def install_app(self, app: "App") -> None:
        run_command(
            self._frappe_call("frappe", "--site", self.config.name, "install-app", app.config.name),
            cwd=self.bench.sites_path,
            stream_output=True,
        )
        self.bench.reload_workers(raises=True)

    def uninstall_app(self, app: "App", force: bool = False) -> None:
        cmd = self._frappe_call("frappe", "--site", self.config.name, "uninstall-app", app.config.name, "--yes", "--no-backup")
        if force:
            cmd.append("--force")
        run_command(cmd, cwd=self.bench.sites_path, stream_output=True)
        self.bench.reload_workers(raises=True)

    def list_apps(self) -> list[str]:
        import subprocess

        result = subprocess.run(
            self._frappe_call("frappe", "--site", self.config.name, "list-apps"),
            cwd=str(self.bench.sites_path),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        return [line.split()[0] for line in result.stdout.splitlines() if line.strip()]

    def migrate(self, skip_failing: bool = False) -> None:
        cmd = self._frappe_call("frappe", "--site", self.config.name, "migrate")
        if skip_failing:
            cmd.append("--skip-failing")
        run_command(cmd, cwd=self.bench.sites_path, stream_output=True)
