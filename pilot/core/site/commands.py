from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
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
        engine = db_type or self.site.bench.config.db_type
        cmd = self.site._frappe_call(
            "frappe",
            "--site",
            self.site.config.name,
            "new-site",
            self.site.config.name,
        )
        cmd += ["--admin-password", self.site.config.admin_password]
        cmd += self.db_args(engine)
        # frappe would name the database itself, at random; pilot names it so the setup
        # account can be granted that database and nothing else.
        database = f"_{secrets.token_hex(8)}" if engine == "mariadb" else ""
        if database:
            cmd += ["--db-name", database]
        with self.setup_credentials(engine, database) as credentials:
            run_command(cmd + credentials, cwd=self.site.bench.sites_path, stream_output=True)

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
        with self.setup_credentials(self.site.bench.config.db_type) as credentials:
            run_command(cmd + credentials, cwd=self.site.bench.sites_path, stream_output=True)

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
        with self.setup_credentials(self.site.bench.config.db_type) as credentials:
            run_command(cmd + credentials, cwd=self.site.bench.sites_path, stream_output=True)

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

    def clear_cache(self) -> None:
        cmd = self.site._frappe_call("frappe", "--site", self.site.config.name, "clear-cache")
        result = run_command(cmd, cwd=self.site.bench.sites_path, stream_output=True)
        if result.returncode != 0:
            raise BenchError(f"Failed to clear cache for {self.site.config.name}")

    def build_assets(self, app: str | None = None, force: bool = True) -> None:
        try:
            active = self.site.active_apps()
        except Exception as exc:
            raise BenchError(
                f"Cannot determine installed apps for site '{self.site.config.name}': {exc}"
            ) from exc

        if app:
            if app not in active:
                raise BenchError(
                    f"App '{app}' is not installed on site '{self.site.config.name}'. "
                    f"Installed apps: {', '.join(active) or '(none)'}"
                )
            apps = [app]
        else:
            apps = active

        if not apps:
            return

        self.site.bench.rebuild_assets(apps=apps, force=force)

    @contextmanager
    def setup_credentials(self, db_type: str, database: str = "") -> Iterator[list[str]]:
        """Database credential arguments for one frappe setup command.

        MariaDB gets a throwaway account scoped to that site's database, dropped as soon
        as the command returns: frappe reads the credential from argv, where every local
        process can see it, and the admin password is far too valuable for that.
        """
        if db_type != "mariadb":
            yield self.site.bench.db_root_args
            return
        from pilot.core.database import site_database_name
        from pilot.managers.database import MariaDBManager

        if not database:
            try:
                database = site_database_name(self.site.bench.path, self.site.config.name)
            except FileNotFoundError:
                database = ""
        if not database:
            raise BenchError(
                f"Cannot determine the database for site '{self.site.config.name}'; refusing to "
                "run frappe with the root database password on the command line."
            )
        manager = MariaDBManager(self.site.bench.config.mariadb)
        with manager.temporary_setup_user(database) as (user, password):
            yield ["--db-root-username", user, "--db-root-password", password]

    def db_args(self, db_type: str) -> list[str]:
        """How to reach the server. Credentials come from setup_credentials."""
        if db_type == "postgres":
            return self.postgres_db_args()
        if db_type == "sqlite":
            return ["--db-type", "sqlite"]

        from pilot.managers.database import MariaDBManager

        socket_path = MariaDBManager(self.site.bench.config.mariadb)._detect_socket()
        return self.mariadb_db_args(socket_path)

    def mariadb_db_args(self, socket_path: str) -> list[str]:
        mariadb = self.site.bench.config.mariadb
        if socket_path:
            return ["--db-socket", socket_path]
        return ["--db-host", mariadb.host, "--db-port", str(mariadb.port)]

    def postgres_db_args(self) -> list[str]:
        postgres = self.site.bench.config.postgres
        return [
            "--db-type",
            "postgres",
            "--db-host",
            postgres.host,
            "--db-port",
            str(postgres.port),
        ]
