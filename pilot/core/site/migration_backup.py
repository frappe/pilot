from __future__ import annotations

import gzip
import json
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.core.site.config import read_site_config
from pilot.exceptions import BenchError
from pilot.utils import make_private_directory, run_command

if TYPE_CHECKING:
    from pilot.core.site import Site


class SiteMigrationBackup:
    """Private database recovery backup taken before a risky migration.

    A recovery artifact owned by a MigrationOperation, distinct from a
    user-visible site backup. MariaDB uses per-table dumps; SQLite and PostgreSQL
    use Frappe's full-database backup and restore commands.
    """

    def __init__(self, site: "Site") -> None:
        self.site = site

    @property
    def directory(self) -> Path:
        return self.site.path / ".migrate"

    @property
    def previous_tables_path(self) -> Path:
        return self.directory / "previous_tables.json"

    @property
    def config_backup_path(self) -> Path:
        return self.directory / "site_config.json"

    @property
    def database_backup_path(self) -> Path:
        return self.directory / "database.sql.gz"

    @property
    def exists(self) -> bool:
        return self.previous_tables_path.exists() or self.database_backup_path.exists()

    def create(self, operation_id: str) -> list[str]:
        """Clear and rebuild the engine-appropriate private database backup.

        Callers must hold the exclusive migration lock and have verified no
        unresolved operation owns the existing directory before calling this.
        MariaDB returns its pre-migration table inventory. Full backups return
        an empty list because table-level recovery does not apply.
        """
        self._validate_owner(operation_id)
        self._reset_directory()
        config = read_site_config(self.site.path)
        database_engine = config.get("db_type", "mariadb")
        if database_engine in {"sqlite", "postgres"}:
            self._create_full_database_backup()
            return []
        if database_engine != "mariadb":
            raise BenchError(f"Migration backups do not support database engine {database_engine!r}")

        return self._create_table_backup(config)

    def _create_table_backup(self, config: dict) -> list[str]:
        db_name = config["db_name"]
        tables = self._list_tables(config, db_name)
        self.previous_tables_path.write_text(json.dumps(tables, indent=2), encoding="utf-8")
        self.config_backup_path.write_text(
            json.dumps(config, indent=1), encoding="utf-8"
        )
        for table in tables:
            self._dump_table(config, db_name, table)
        return tables

    def _create_full_database_backup(self) -> None:
        command = self.site._frappe_call(
            "frappe",
            "--site",
            self.site.config.name,
            "backup",
            "--backup-path-db",
            str(self.database_backup_path),
            "--backup-path-conf",
            str(self.config_backup_path),
            "--ignore-backup-conf",
        )
        run_command(command, cwd=self.site.bench.sites_path, stream_output=True)
        if not self.database_backup_path.is_file():
            raise BenchError(f"Frappe did not create a database backup for {self.site.config.name}")

    @property
    def previous_tables(self) -> list[str]:
        if not self.previous_tables_path.exists():
            return []
        return json.loads(self.previous_tables_path.read_text(encoding="utf-8"))

    def restore(self, tables: list[str]) -> None:
        """Restore a full database backup or selected MariaDB table dumps."""
        if self.database_backup_path.is_file():
            self.site.restore(str(self.database_backup_path))
            return
        config = read_site_config(self.site.path)
        db_name = config["db_name"]
        previous = set(self.previous_tables)
        restore_tables = tables or list(previous)

        for table in restore_tables:
            dump = self._dump_path(table)
            if dump.exists():
                self._import_dump(config, db_name, dump)

        created = [t for t in self._list_tables(config, db_name) if t not in previous]
        if created:
            self._drop_tables(config, db_name, created)

    def discard(self) -> None:
        if self.directory.exists():
            shutil.rmtree(self.directory)

    def _reset_directory(self) -> None:
        if self.directory.exists():
            shutil.rmtree(self.directory)
        make_private_directory(self.directory, parents=True)

    def _validate_owner(self, operation_id: str) -> None:
        owners = self.site.bench.migrations.unresolved_for_site(self.site.config.name)
        if len(owners) != 1 or owners[0].id != operation_id:
            raise BenchError(
                f"Migration recovery directory for {self.site.config.name} is owned by "
                "another unresolved operation."
            )

    def _dump_path(self, table: str) -> Path:
        return self.directory / f"{table}.sql.gz"

    def _list_tables(self, config: dict, db_name: str) -> list[str]:
        output = subprocess.run(
            [self._cli(), *self._conn_args(config), "--batch", "--skip-column-names", db_name, "-e",
             "SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return [line.split("\t")[0] for line in output.splitlines() if line.strip()]

    def _dump_table(self, config: dict, db_name: str, table: str) -> None:
        argv = [
            self._dump_cli(),
            *self._conn_args(config),
            "--single-transaction",
            "--quick",
            "--no-tablespaces",
            db_name,
            table,
        ]
        with gzip.open(self._dump_path(table), "wb") as compressed:
            process = subprocess.Popen(argv, stdout=subprocess.PIPE)
            if process.stdout is None:
                raise BenchError("Database dump output pipe was not created")
            shutil.copyfileobj(process.stdout, compressed)
            process.stdout.close()
            if process.wait() != 0:
                raise BenchError(f"Failed to dump table {table} for {self.site.config.name}")

    def _import_dump(self, config: dict, db_name: str, dump: Path) -> None:
        argv = [self._cli(), *self._conn_args(config), db_name]
        with gzip.open(dump, "rb") as compressed:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE)
            if process.stdin is None:
                raise BenchError("Database import input pipe was not created")
            shutil.copyfileobj(compressed, process.stdin)
            process.stdin.close()
            if process.wait() != 0:
                raise BenchError(f"Failed to import {dump.name} for {self.site.config.name}")

    def _drop_tables(self, config: dict, db_name: str, tables: list[str]) -> None:
        statements = ";".join(f"DROP TABLE IF EXISTS `{t.replace('`', '')}`" for t in tables)
        subprocess.run(
            [self._cli(), *self._conn_args(config), db_name, "-e",
             f"SET FOREIGN_KEY_CHECKS=0;{statements}"],
            check=True,
        )

    @staticmethod
    def _cli() -> str:
        cli = shutil.which("mariadb") or shutil.which("mysql")
        if not cli:
            raise BenchError("No mariadb/mysql client found for migration backup operations.")
        return cli

    @staticmethod
    def _dump_cli() -> str:
        cli = shutil.which("mariadb-dump") or shutil.which("mysqldump")
        if not cli:
            raise BenchError("No mariadb-dump/mysqldump found for migration backup operations.")
        return cli

    def _conn_args(self, config: dict) -> list[str]:
        args = [f"--user={config['db_name']}", f"--password={config['db_password']}"]
        socket = config.get("db_socket") or self.site.bench.config.mariadb.socket_path
        if socket:
            args.append(f"--socket={socket}")
        else:
            args += [
                f"--host={config.get('db_host') or 'localhost'}",
                f"--port={int(config.get('db_port') or 3306)}",
            ]
        return args
