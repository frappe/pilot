from __future__ import annotations

import gzip
import json
import shutil
import subprocess
import tempfile
import zlib
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import IO, TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.internal.command import run_command
from pilot.internal.private_files import PrivateDirectory

if TYPE_CHECKING:
    from pilot.core.site import Site
    from pilot.integrations.s3.base import S3


class GzipReader:
    """Gzip-compresses a byte stream as boto3 reads it, so a dump reaches S3 without touching the disk."""

    def __init__(self, source) -> None:
        self.source = source
        self.compressor = zlib.compressobj(wbits=31)
        self.buffer = b""
        self.is_done = False

    def read(self, size: int = -1) -> bytes:
        while not self.is_done and (size < 0 or len(self.buffer) < size):
            chunk = self.source.read(1 << 20)
            self.buffer += self.compressor.compress(chunk) if chunk else self.compressor.flush()
            self.is_done = not chunk
        cut = len(self.buffer) if size < 0 else size
        data, self.buffer = self.buffer[:cut], self.buffer[cut:]
        return data


class GunzipWriter:
    """Decompresses gzip bytes as boto3 writes them into `target`, so a dump streams from S3
    into the database client without touching the disk."""

    def __init__(self, target) -> None:
        self.target = target
        self.decompressor = zlib.decompressobj(wbits=31)

    def write(self, data: bytes) -> int:
        self.target.write(self.decompressor.decompress(data))
        return len(data)

    def flush(self) -> None:
        self.target.write(self.decompressor.flush())


class SiteMigrationBackup:
    """Private database recovery backup taken before a risky migration.

    A recovery artifact owned by a MigrationOperation, distinct from a
    user-visible site backup. MariaDB uses per-table dumps; SQLite and PostgreSQL
    use Frappe's full-database backup and restore commands. When the bench bucket
    stores migration backups, MariaDB dumps stream to and from S3, and only the table
    inventory, the config copy and `offsite.json` stay on disk.
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
    def offsite_path(self) -> Path:
        return self.directory / "offsite.json"

    @property
    def exists(self) -> bool:
        return self.previous_tables_path.exists() or self.database_backup_path.exists()

    @property
    def is_stored_in_s3(self) -> bool:
        """Whether a new backup of this site goes to the bench bucket instead of the local disk."""
        engine = self.site.config_file.read().get("db_type", "mariadb")
        return engine == "mariadb" and self.site.bench.config.s3.stores_migration_backups

    @property
    def offsite(self) -> dict | None:
        """Bucket and key prefix of the dumps this backup keeps in S3, or None for a local backup."""
        if not self.offsite_path.exists():
            return None
        try:
            location = json.loads(self.offsite_path.read_text(encoding="utf-8"))
            return {"bucket": str(location["bucket"]), "prefix": str(location["prefix"])}
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise BenchError(
                f"Cannot read the S3 location of the migration backup for {self.site.config.name}: {error}"
            ) from error

    def create(self, operation_id: str) -> list[str]:
        """Clear and rebuild the engine-appropriate private database backup.

        Callers must hold the exclusive migration lock and have verified no
        unresolved operation owns the existing directory before calling this.
        MariaDB returns its pre-migration table inventory. Full backups return
        an empty list because table-level recovery does not apply.
        """
        self._validate_owner(operation_id)
        self._reset_directory()
        config = self.site.config_file.read()
        database_engine = config.get("db_type", "mariadb")
        if database_engine in {"sqlite", "postgres"}:
            self._create_full_database_backup()
            return []
        if database_engine != "mariadb":
            raise BenchError(f"Migration backups do not support database engine {database_engine!r}")

        if self.is_stored_in_s3:
            bucket = self.site.bench.config.s3.bucket
            prefix = f"sites/{self.site.config.name}/migration_backups/{operation_id}/"
            self.offsite_path.write_text(json.dumps({"bucket": bucket, "prefix": prefix}), encoding="utf-8")
        return self._create_table_backup(config)

    def _create_table_backup(self, config: dict) -> list[str]:
        db_name = config["db_name"]
        tables = self._list_tables(config, db_name)
        self.previous_tables_path.write_text(json.dumps(tables, indent=2), encoding="utf-8")
        self.config_backup_path.write_text(json.dumps(config, indent=1), encoding="utf-8")
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
        try:
            tables = json.loads(self.previous_tables_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise BenchError(
                f"Cannot read migration table inventory for {self.site.config.name}: {error}"
            ) from error
        if (
            not isinstance(tables, list)
            or not tables
            or any(not isinstance(table, str) or not table for table in tables)
        ):
            raise BenchError(f"Invalid migration table inventory for {self.site.config.name}")
        return tables

    def restore(self, tables: list[str]) -> None:
        """Restore a full database backup or selected MariaDB table dumps."""
        if self.database_backup_path.is_file():
            self.site.restore(str(self.database_backup_path))
            return
        config = self.site.config_file.read()
        db_name = config["db_name"]
        previous = set(self.previous_tables)
        for dump in self.get_restore_dumps(tables, previous):
            if self.offsite:
                self._import_offsite_dump(config, db_name, dump)
            else:
                self._import_dump(config, db_name, dump)

        created = [t for t in self._list_tables(config, db_name) if t not in previous]
        if created:
            self._drop_tables(config, db_name, created)

    def get_restore_dumps(self, tables: list[str], previous: set[str]) -> list[Path]:
        """Validate every required dump before importing any table."""
        dumps = [self._dump_path(table) for table in (tables or sorted(previous)) if table in previous]
        stored = self._offsite_names() if self.offsite else {dump.name for dump in dumps if dump.is_file()}
        for dump in dumps:
            if dump.name not in stored:
                raise BenchError(f"Missing migration table dump for {self.site.config.name}: {dump.name}")
        return dumps

    def discard(self) -> None:
        if location := self.offsite:
            s3 = self._s3()
            for key in s3.list_objects(location["bucket"], location["prefix"]):
                s3.delete_object(location["bucket"], key)
        if self.directory.exists():
            shutil.rmtree(self.directory)

    def _reset_directory(self) -> None:
        self.discard()
        PrivateDirectory(self.directory).create(parents=True)

    def _s3(self) -> "S3":
        from pilot.integrations.s3.base import S3

        return S3.from_config(self.site.bench.config.s3)

    def _offsite_names(self) -> set[str]:
        location = self.offsite or {}
        keys = self._s3().list_objects(location["bucket"], location["prefix"])
        return {key.removeprefix(location["prefix"]) for key in keys}

    def _import_offsite_dump(self, config: dict, db_name: str, dump: Path) -> None:
        """Stream one dump from S3 through gunzip into the database client. A failed import
        is safe to repeat, because each dump drops and recreates its table."""
        location = self.offsite or {}

        def feed(client_input) -> None:
            writer = GunzipWriter(client_input)
            key = location["prefix"] + dump.name
            self._s3().download_stream(location["bucket"], key, writer, low_memory=True)
            writer.flush()

        self._run_import(config, db_name, dump.name, feed)

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
            [
                self._cli(),
                *self._conn_args(config),
                "--batch",
                "--skip-column-names",
                db_name,
                "-e",
                "SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'",
            ],
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
        if location := self.offsite:
            self._upload_dump(argv, table, location)
            return
        with gzip.open(self._dump_path(table), "wb") as compressed:
            process = subprocess.Popen(argv, stdout=subprocess.PIPE)
            if process.stdout is None:
                raise BenchError("Database dump output pipe was not created")
            shutil.copyfileobj(process.stdout, compressed)
            process.stdout.close()
            if process.wait() != 0:
                raise BenchError(f"Failed to dump table {table} for {self.site.config.name}")

    def _upload_dump(self, argv: list[str], table: str, location: dict) -> None:
        """Stream one table dump into S3. A dump that fails part way deletes its object."""
        s3 = self._s3()
        key = location["prefix"] + self._dump_path(table).name
        process = subprocess.Popen(argv, stdout=subprocess.PIPE)
        if process.stdout is None:
            raise BenchError("Database dump output pipe was not created")
        try:
            s3.upload_stream(location["bucket"], key, GzipReader(process.stdout), low_memory=True)
        except Exception:
            process.kill()
            raise
        finally:
            process.stdout.close()
        if process.wait() != 0:
            s3.delete_object(location["bucket"], key)
            raise BenchError(f"Failed to dump table {table} for {self.site.config.name}")

    def _import_dump(self, config: dict, db_name: str, dump: Path) -> None:
        def feed(client_input) -> None:
            with gzip.open(dump, "rb") as compressed:
                shutil.copyfileobj(compressed, client_input)

        self._run_import(config, db_name, dump.name, feed)

    def _run_import(self, config: dict, db_name: str, name: str, feed: Callable[[IO[bytes]], None]) -> None:
        """Run the database client and let `feed` write the SQL into its input."""
        argv = [self._cli(), *self._conn_args(config), db_name]
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(argv, stdin=subprocess.PIPE, stderr=errors)
            if process.stdin is None:
                raise BenchError("Database import input pipe was not created")
            try:
                feed(process.stdin)
            except BrokenPipeError:
                pass  # the client exited early; its exit code and error tell why
            except BaseException:
                process.kill()  # do not let the client commit a partial input
                process.wait()
                raise
            finally:
                with suppress(BrokenPipeError):
                    process.stdin.close()
            if process.wait() != 0:
                errors.seek(0)
                reason = errors.read().decode(errors="replace").strip()[-500:]
                raise BenchError(f"Failed to import {name} for {self.site.config.name}: {reason}")

    def _drop_tables(self, config: dict, db_name: str, tables: list[str]) -> None:
        statements = ";".join(f"DROP TABLE IF EXISTS `{t.replace('`', '')}`" for t in tables)
        subprocess.run(
            [self._cli(), *self._conn_args(config), db_name, "-e", f"SET FOREIGN_KEY_CHECKS=0;{statements}"],
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
