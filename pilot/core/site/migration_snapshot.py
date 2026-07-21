from __future__ import annotations

import gzip
import json
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.core.site.config import read_site_config
from pilot.exceptions import BenchError
from pilot.utils import make_private_directory

if TYPE_CHECKING:
    from pilot.core.site import Site


class SnapshotUnsupported(BenchError):
    """Selective table snapshots are not available for this site's engine."""


class SiteMigrationSnapshot:
    """Private per-table recovery snapshot taken before a risky migration.

    A recovery artifact owned by a MigrationOperation, never a user-visible
    backup. MariaDB gets selective per-table dumps; other engines are
    unsupported in v1 (Retry/manual repair remain, but not Revert).
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
    def config_snapshot_path(self) -> Path:
        return self.directory / "site_config.json"

    @property
    def exists(self) -> bool:
        return self.previous_tables_path.exists()

    def create(self) -> list[str]:
        """Clear and rebuild the snapshot: table inventory, per-table dumps, config.

        Callers must hold the exclusive migration lock and have verified no
        unresolved operation owns the existing directory before calling this.
        Returns the pre-migration table inventory.
        """
        config = read_site_config(self.site.path)
        if config.get("db_type", "mariadb") != "mariadb":
            raise SnapshotUnsupported(
                f"Selective snapshots require MariaDB; {self.site.config.name} uses "
                f"{config.get('db_type')}"
            )

        self._reset_directory()
        db_name = config["db_name"]
        tables = self._list_tables(config, db_name)
        self.previous_tables_path.write_text(json.dumps(tables, indent=2), encoding="utf-8")
        self.config_snapshot_path.write_text(
            json.dumps(config, indent=1), encoding="utf-8"
        )
        for table in tables:
            self._dump_table(config, db_name, table)
        return tables

    def previous_tables(self) -> list[str]:
        if not self.previous_tables_path.exists():
            return []
        return json.loads(self.previous_tables_path.read_text(encoding="utf-8"))

    def restore(self, tables: list[str]) -> None:
        """Import the given tables from their dumps, then drop migration-created tables."""
        config = read_site_config(self.site.path)
        db_name = config["db_name"]
        previous = set(self.previous_tables())
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
            shutil.copyfileobj(process.stdout, compressed)
            process.stdout.close()
            if process.wait() != 0:
                raise BenchError(f"Failed to dump table {table} for {self.site.config.name}")

    def _import_dump(self, config: dict, db_name: str, dump: Path) -> None:
        argv = [self._cli(), *self._conn_args(config), db_name]
        with gzip.open(dump, "rb") as compressed:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE)
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
            raise BenchError("No mariadb/mysql client found for snapshot operations.")
        return cli

    @staticmethod
    def _dump_cli() -> str:
        cli = shutil.which("mariadb-dump") or shutil.which("mysqldump")
        if not cli:
            raise BenchError("No mariadb-dump/mysqldump found for snapshot operations.")
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
