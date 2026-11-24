from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pilot.core.site.clone_database import stream_database
from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.config import MariaDBConfig
    from pilot.core.site import Site
    from pilot.managers.database import MariaDBManager

LOCAL_HOSTS = frozenset({"", "localhost", "127.0.0.1", "::1"})


def get_rows(manager: MariaDBManager, sql: str) -> list[str]:
    """The rows of a query as the client prints them in batch mode. The first line names the columns."""
    return manager.run_admin_sql(sql).splitlines()[1:]


def quote_identifier(name: str) -> str:
    """Quote a database name for SQL."""
    return "`" + name.replace("`", "``") + "`"


def get_site_label(site: Site) -> str:
    """The bench and the site, as messages name a site."""
    return f"{site.bench.config.name}/{site.config.name}"


@dataclass(frozen=True)
class SiteDatabase:
    """A site's database and login on Pilot's local MariaDB, as its site_config.json names them."""

    site: Site
    name: str
    user: str
    password: str

    @classmethod
    def for_site(cls, site: Site, local: MariaDBManager) -> SiteDatabase | None:
        """The site's database, or None when the site keeps it in another engine, such as SQLite."""
        label = get_site_label(site)
        try:
            config = site.config_file.read()
        except (OSError, ValueError) as error:
            raise BenchError(f"Pilot cannot read the site_config.json of {label}: {error}") from error

        if (config.get("db_type") or "mariadb") != "mariadb":
            return None
        if not config.get("db_name") or not config.get("db_password"):
            raise BenchError(f"The site_config.json of {label} has no db_name or no db_password.")
        if not cls.is_on_server(config, local):
            where = config.get("db_socket") or f"{config.get('db_host')}:{config.get('db_port')}"
            raise BenchError(
                f"{label} uses the MariaDB server at {where}, not this host's server. "
                "Pilot moves only the databases of this host's server."
            )
        return cls(site, config["db_name"], config.get("db_user") or config["db_name"], config["db_password"])

    @staticmethod
    def is_on_server(config: dict, local: MariaDBManager) -> bool:
        """Whether a site config connects to the local server: its socket, or its host and port."""
        if config.get("db_socket"):
            return config["db_socket"] == local.socket_path
        host = str(config.get("db_host") or "")
        port = config.get("db_port")
        is_local_host = host in LOCAL_HOSTS or host == local.config.host
        return is_local_host and (port in (None, "") or str(port) == str(local.config.port))

    @property
    def label(self) -> str:
        """The bench and the site."""
        return get_site_label(self.site)

    def create(self, remote: MariaDBManager, host: str) -> None:
        """Create the empty database and the site's login at `host` on the remote server."""
        user = f"{remote._sql_quote(self.user)}@{remote._sql_quote(host)}"
        database = quote_identifier(self.name)
        remote.run_admin_sql(
            f"CREATE DATABASE {database} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
            f"CREATE USER {user} IDENTIFIED BY {remote._sql_quote(self.password)};\n"
            f"GRANT ALL PRIVILEGES ON {database}.* TO {user};"
        )

    def drop(self, remote: MariaDBManager, host: str) -> None:
        """Drop the database and the login that `create` made."""
        user = f"{remote._sql_quote(self.user)}@{remote._sql_quote(host)}"
        remote.run_admin_sql(
            f"DROP DATABASE IF EXISTS {quote_identifier(self.name)};\nDROP USER IF EXISTS {user};"
        )

    def copy(self, local: MariaDBManager, remote: MariaDBManager) -> None:
        """Pipe a consistent dump from the local server into the remote one, with no file in between."""
        dump = [
            "mariadb-dump",
            "-u",
            local.config.admin_user,
            *local.server_arguments,
            "--single-transaction",
            "--quick",
            "--routines",
            "--triggers",
            "--events",
            "--hex-blob",
            "--default-character-set=utf8mb4",
            "--max-allowed-packet=512M",
            "--",
            self.name,
        ]
        restore = [
            *remote._client_command(),
            "--default-character-set=utf8mb4",
            "--max-allowed-packet=512M",
            "--",
            self.name,
        ]
        try:
            stream_database(dump, restore, self.get_client_env(local), self.get_client_env(remote))
        except BenchError as error:
            raise BenchError(f"Copying the database of {self.label} failed: {error}") from error

    @staticmethod
    def get_client_env(manager: MariaDBManager) -> dict[str, str]:
        """The environment that gives a client the admin password, which must stay off the command line."""
        return {**os.environ, "MYSQL_PWD": manager.config.root_password}

    def get_objects(self, manager: MariaDBManager) -> set[str]:
        """The tables, views, sequences, routines, triggers and events of the database on a server."""
        schema = manager._sql_quote(self.name)
        return set(
            get_rows(
                manager,
                f"SELECT CONCAT(TABLE_TYPE, ' ', TABLE_NAME) FROM information_schema.TABLES WHERE TABLE_SCHEMA = {schema}\n"
                f"UNION ALL SELECT CONCAT(ROUTINE_TYPE, ' ', ROUTINE_NAME) FROM information_schema.ROUTINES "
                f"WHERE ROUTINE_SCHEMA = {schema}\n"
                f"UNION ALL SELECT CONCAT('TRIGGER ', TRIGGER_NAME) FROM information_schema.TRIGGERS "
                f"WHERE TRIGGER_SCHEMA = {schema}\n"
                f"UNION ALL SELECT CONCAT('EVENT ', EVENT_NAME) FROM information_schema.EVENTS WHERE EVENT_SCHEMA = {schema};",
            )
        )

    def verify(self, local: MariaDBManager, remote: MariaDBManager) -> None:
        """Raise unless the copy has the same objects as the local database."""
        source, copy = self.get_objects(local), self.get_objects(remote)
        if source == copy:
            return
        missing = ", ".join(sorted(source - copy)) or "none"
        unexpected = ", ".join(sorted(copy - source)) or "none"
        raise BenchError(
            f"The copy of the database of {self.label} does not match the local one. "
            f"Missing on the remote server: {missing}. Only on the remote server: {unexpected}."
        )

    def point_site_at(self, remote: MariaDBConfig) -> None:
        """Make the site connect to the remote server, as a site created there does."""
        with self.site.config_file.updating() as config:
            config["db_host"] = remote.host
            config["db_port"] = remote.port
            config.pop("db_socket", None)
