from __future__ import annotations

import os
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config.worktree import DB_NAME_PATTERN
from pilot.exceptions import BenchError
from pilot.managers.database import MariaDBManager, PostgresManager
from pilot.managers.platform import which

if TYPE_CHECKING:
    from pilot.config import BenchConfig, MariaDBConfig, PostgresConfig

# Creating or dropping a database with hundreds of tables can take longer than a query.
_ADMIN_TIMEOUT_SECONDS = 300


@dataclass
class ClientCommand:
    """A database client's argv, with its password kept out of argv, in `env`."""

    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)


class SQLiteSiteDatabase:
    """A clone's SQLite file, inside the overlay and removed with it."""

    def copy(self, source: Path, target: Path, source_config: dict, config: dict) -> None:
        """Copy through SQLite's backup API, which includes writes still in the WAL."""
        source_db = source / "db" / f"{source_config['db_name']}.db"
        if not source_db.exists():
            raise BenchError(f"Database {source_db} not found.")
        target_db = target / "db" / f"{config['db_name']}.db"
        with closing(sqlite3.connect(source_db)) as reader, closing(sqlite3.connect(target_db)) as writer:
            reader.backup(writer)

    def drop(self, db_name: str) -> None:
        """Nothing outside the overlay to drop."""


class MariaDBSiteDatabase:
    """A clone's MariaDB database and its account, both named `db_name`."""

    def __init__(self, config: "MariaDBConfig") -> None:
        self.config = config
        self.manager = MariaDBManager(config)

    def copy(self, source: Path, target: Path, source_config: dict, config: dict) -> None:
        """Create the database and account, then stream a dump of the base site's database into it.
        `--single-transaction` reads InnoDB tables from one snapshot while the base site keeps running."""
        name = checked_db_name(config["db_name"])
        account = f"'{name}'@'%'"
        run_admin_sql(
            self.manager,
            f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;\n"
            f"CREATE USER {account} IDENTIFIED BY {MariaDBManager._sql_quote(config['db_password'])};\n"
            f"GRANT ALL PRIVILEGES ON `{name}`.* TO {account};",
        )
        dump = self.client(_binary("mariadb-dump", "mysqldump"), source_config)
        dump.argv += ["--single-transaction", "--quick", "--no-tablespaces", source_config["db_name"]]
        load = self.client(_binary("mariadb", "mysql"), config)
        load.argv.append(name)
        pipe(dump, load)

    def drop(self, db_name: str) -> None:
        name = checked_db_name(db_name)
        run_admin_sql(self.manager, f"DROP DATABASE IF EXISTS `{name}`;\nDROP USER IF EXISTS '{name}'@'%';")

    def client(self, binary: str, site_config: dict) -> ClientCommand:
        """A client logged in as a site's own account. Over TCP unless the site uses a socket:
        without `--protocol=TCP` the client takes host `localhost` to mean the default socket."""
        argv = [binary, f"--user={site_config.get('db_user') or site_config['db_name']}"]
        if socket := site_config.get("db_socket"):
            argv.append(f"--socket={socket}")
        else:
            host = site_config.get("db_host") or self.config.host
            port = site_config.get("db_port") or self.config.port
            argv += ["--protocol=TCP", f"--host={host}", f"--port={port}"]
        return ClientCommand(argv, {"MYSQL_PWD": site_config["db_password"]})


class PostgresSiteDatabase:
    """A clone's PostgreSQL database and the role that owns it, both named `db_name`."""

    def __init__(self, config: "PostgresConfig") -> None:
        self.config = config
        self.manager = PostgresManager(config)

    def copy(self, source: Path, target: Path, source_config: dict, config: dict) -> None:
        """Create the role and database, then stream a dump of the base site's database into it.

        pg_dump reads one consistent snapshot. Extensions are left out: the clone's role may
        not create them, and Frappe adds its only one, pg_stat_statements, as best effort."""
        name = checked_db_name(config["db_name"])
        run_admin_sql(
            self.manager,
            f'CREATE ROLE "{name}" WITH LOGIN PASSWORD {PostgresManager._sql_quote(config["db_password"])};\n'
            f'CREATE DATABASE "{name}" OWNER "{name}";',
        )
        dump = self.client(_binary("pg_dump"), source_config)
        dump.argv += ["--no-owner", "--no-privileges", "--extension=plpgsql", source_config["db_name"]]
        load = self.client(_binary("psql"), config)
        load.argv += ["--no-psqlrc", "--quiet", "--set=ON_ERROR_STOP=1", f"--dbname={name}"]
        pipe(dump, load)

    def drop(self, db_name: str) -> None:
        name = checked_db_name(db_name)
        run_admin_sql(
            self.manager, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE);\nDROP ROLE IF EXISTS "{name}";'
        )

    def client(self, binary: str, site_config: dict) -> ClientCommand:
        """A client logged in as a site's own role."""
        host = site_config.get("db_host") or self.config.host
        port = site_config.get("db_port") or self.config.port
        user = site_config.get("db_user") or site_config["db_name"]
        return ClientCommand(
            [binary, f"--host={host}", f"--port={port}", f"--username={user}"],
            {"PGPASSWORD": site_config["db_password"]},
        )


SiteDatabase = SQLiteSiteDatabase | MariaDBSiteDatabase | PostgresSiteDatabase


def get_site_database(config: "BenchConfig") -> SiteDatabase:
    """How a worktree copies and drops its site's database on this bench's engine."""
    if config.db_type == "sqlite":
        return SQLiteSiteDatabase()
    if config.db_type == "mariadb":
        return MariaDBSiteDatabase(config.mariadb)
    if config.db_type == "postgres":
        return PostgresSiteDatabase(config.postgres)
    raise BenchError(f"Worktrees do not support database type '{config.db_type}'.")


def checked_db_name(name: str) -> str:
    """Only a name of the form Pilot generates reaches CREATE or DROP."""
    if not DB_NAME_PATTERN.match(name):
        raise BenchError(f"Refusing to touch database '{name}': Pilot did not generate that name.")
    return name


def run_admin_sql(manager: MariaDBManager | PostgresManager, sql: str) -> None:
    try:
        manager.run_admin_sql(sql, timeout=_ADMIN_TIMEOUT_SECONDS)
    except subprocess.CalledProcessError as error:
        raise BenchError(f"Database command failed: {(error.stderr or '').strip()}") from error


def pipe(dump: ClientCommand, load: ClientCommand) -> None:
    """Stream `dump`'s output into `load`, with no file in between. Fails if either fails."""
    with tempfile.TemporaryFile() as dump_errors:
        producer = subprocess.Popen(
            dump.argv, stdout=subprocess.PIPE, stderr=dump_errors, env={**os.environ, **dump.env}
        )
        consumer = subprocess.Popen(
            load.argv,
            stdin=producer.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env={**os.environ, **load.env},
        )
        if producer.stdout is not None:
            # The consumer holds its own copy; closing ours lets the dump stop if the load exits.
            producer.stdout.close()
        _, load_errors = consumer.communicate()
        producer.wait()
        dump_errors.seek(0)
        dump_error_text = dump_errors.read().decode(errors="replace").strip()
    if consumer.returncode != 0:
        raise BenchError(f"Loading the database copy failed: {load_errors.decode(errors='replace').strip()}")
    if producer.returncode != 0:
        raise BenchError(f"Dumping the base site's database failed: {dump_error_text}")


def _binary(*names: str) -> str:
    for name in names:
        if found := which(name):
            return found
    raise BenchError(f"{' or '.join(names)} not found. Install the database client tools.")
