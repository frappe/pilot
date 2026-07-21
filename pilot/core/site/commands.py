from __future__ import annotations

from typing import TYPE_CHECKING

from pilot.exceptions import BenchError, MigrateError
from pilot.utils import run_command

if TYPE_CHECKING:
    from pilot.core.site import Site


class SiteCommands:
    def __init__(self, site: "Site") -> None:
        self.site = site

    def create(self, db_type: str | None = None) -> None:
        if (
            not isinstance(self.site.config.admin_password, str)
            or not self.site.config.admin_password.strip()
        ):
            raise BenchError("Site Administrator password must not be empty.")
        cmd = self.site._frappe_call(
            "frappe",
            "--site",
            self.site.config.name,
            "new-site",
            self.site.config.name,
        )
        cmd += ["--admin-password", self.site.config.admin_password]
        cmd += self.db_args(db_type or self.site.bench.config.db_type)
        run_command(cmd, cwd=self.site.bench.sites_path, stream_output=True)

    def restore(
        self,
        db_file: str,
        public_files: str | None = None,
        private_files: str | None = None,
    ) -> None:
        cmd = self.site._frappe_call("frappe", "--site", self.site.config.name, "restore", db_file)
        if public_files:
            cmd += ["--with-public-files", public_files]
        if private_files:
            cmd += ["--with-private-files", private_files]
        cmd += self.site.bench.db_root_args
        run_command(cmd, cwd=self.site.bench.sites_path, stream_output=True)

    def reinstall(self, admin_password: str) -> None:
        if not isinstance(admin_password, str) or not admin_password.strip():
            raise BenchError("Site Administrator password must not be empty.")
        cmd = self.site._frappe_call(
            "frappe",
            "--site",
            self.site.config.name,
            "reinstall",
            "--yes",
            "--admin-password",
            admin_password,
        )
        cmd += self.site.bench.db_root_args
        run_command(cmd, cwd=self.site.bench.sites_path, stream_output=True)

    def migrate(self, skip_failing: bool) -> str:
        """Run migration, streaming output live and returning the full captured output."""
        cmd = self.site._frappe_call("frappe", "--site", self.site.config.name, "migrate")
        if skip_failing:
            cmd.append("--skip-failing")
        result = run_command(cmd, cwd=self.site.bench.sites_path, tee_output=True)
        if result.returncode != 0:
            raise MigrateError(
                f"Migration failed for {self.site.config.name}",
                output=result.stdout,
                returncode=result.returncode,
            )
        return result.stdout

    def db_args(self, db_type: str) -> list[str]:
        if db_type == "postgres":
            return self.postgres_db_args()
        if db_type == "sqlite":
            return ["--db-type", "sqlite"]

        from pilot.managers.database import MariaDBManager

        socket_path = MariaDBManager(self.site.bench.config.mariadb)._detect_socket()
        return self.mariadb_db_args(socket_path)

    def mariadb_db_args(self, socket_path: str) -> list[str]:
        mariadb = self.site.bench.config.mariadb
        args = ["--db-root-username", mariadb.admin_user]
        if socket_path:
            args += ["--db-socket", socket_path]
            args += ["--db-root-password", mariadb.root_password or "socket_auth"]
        else:
            args += ["--db-host", mariadb.host, "--db-port", str(mariadb.port)]
            if mariadb.root_password:
                args += ["--db-root-password", mariadb.root_password]
        return args

    def postgres_db_args(self) -> list[str]:
        postgres = self.site.bench.config.postgres
        return [
            "--db-type",
            "postgres",
            "--db-host",
            postgres.host,
            "--db-port",
            str(postgres.port),
            "--db-root-username",
            postgres.admin_user,
            "--db-root-password",
            self.site.bench.postgres_root_password,
        ]
