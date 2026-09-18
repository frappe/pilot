from __future__ import annotations

from dataclasses import asdict, replace
from pathlib import Path

from pilot.config import BenchConfig
from pilot.core.database import Database, make_database, make_site_database, site_database_name
from pilot.exceptions import DatabaseError

NO_DATABASE_SERVER = (
    "SQLite is a per-site database file, not a shared server"
)
NOT_SUPPORTED = "The selected engine does not support this operation"


class DatabaseDiagnosticsProvider:
    """Server-level diagnostics for the bench's database, shaped for JSON.

    A SQLite bench has no database server: every site owns a file under
    sites/<site>/db/. Only get_diagnostics() answers for such a bench;
    the rest raise DatabaseError.

    Timestamps stay raw (epoch ms) and sizes stay raw bytes; formatting
    belongs to the consumer.
    """

    def __init__(self, bench_root: Path, database: Database | None = None, engine: str = "mariadb") -> None:
        self._bench_root = bench_root
        if database is not None:
            self._db: Database | None = database
            self._engine = engine
            return
        config = BenchConfig.read(bench_root, validate=False)
        self._engine = config.db_type
        self._db = None if config.db_type == "sqlite" else make_database(config)

    def get_diagnostics(self) -> dict:
        if self._db is None:
            return {"engine": self._engine, "supported": False, "reason": NO_DATABASE_SERVER}
        database = self._require_server()
        binlog = self._optional(database.get_binlog_status)
        return {
            "engine": self._engine,
            "supported": True,
            "active_connections": self._call(database.get_active_connections),
            "lock_waits": asdict(self._call(database.get_lock_waits)),
            "binlog": asdict(binlog) if binlog is not None else None,
            "performance_schema_enabled": self._optional(
                lambda: database.is_performance_schema_enabled
            )
            or False,
        }

    def get_process_list(self, site: str = "") -> list[dict]:
        processes = self._call(self._require_server().get_process_list, self._database_for(site))
        return [asdict(process) for process in processes]

    def kill_process(self, process_id: int) -> None:
        self._call(self._require_server().kill_process, process_id)

    def get_lock_wait_rows(self, site: str = "") -> list[dict]:
        rows = self._call(self._require_server().get_lock_wait_rows, self._database_for(site))
        return [asdict(row) for row in rows]

    def get_time_consuming_queries(self, site: str = "", limit: int = 20, offset: int = 0) -> dict:
        section = self._call(
            self._require_server().get_time_consuming_queries, self._database_for(site), limit, offset
        )
        return asdict(section)

    def get_full_table_scan_queries(self, site: str = "", limit: int = 20, offset: int = 0) -> dict:
        section = self._call(
            self._require_server().get_full_table_scan_queries, self._database_for(site), limit, offset
        )
        return asdict(section)

    def get_unused_indexes(self, site: str = "", limit: int = 20, offset: int = 0) -> dict:
        section = self._call(
            self._require_server().get_unused_indexes, self._database_for(site), limit, offset
        )
        return asdict(section)

    def get_redundant_indexes(self, site: str = "", limit: int = 20, offset: int = 0) -> dict:
        section = self._call(
            self._require_server().get_redundant_indexes, self._database_for(site), limit, offset
        )
        return asdict(section)

    def get_database_size(self, site: str = "") -> dict:
        size = self._call(self._connection_for(site).get_database_size)
        # A site's own user may not be allowed to read the server's data directory.
        if site and size.free_bytes is None and self._db is not None:
            size = replace(size, free_bytes=self._call(self._db.get_free_disk_bytes))
        return asdict(size)

    def get_table_sizes(self, site: str) -> list[dict]:
        if not site:
            raise DatabaseError("A site is required to break sizes down by table.")
        return [asdict(table) for table in self._call(self._connection_for(site).get_table_sizes)]

    def get_binlog_files(self) -> list[dict]:
        return [asdict(file) for file in self._call(self._require_server().get_binlog_files)]

    def purge_binlogs(self, up_to: str) -> None:
        self._call(self._require_server().purge_binlogs, up_to)

    def _require_server(self) -> Database:
        if self._db is None:
            raise DatabaseError(NO_DATABASE_SERVER)
        return self._db

    def _connection_for(self, site: str) -> Database:
        """Sizes are read through a connection bound to the site's own database,
        so the engine scopes them without a database name reaching the query -
        and so PostgreSQL, whose table catalog is per-database, can see them."""
        if not site:
            return self._require_server()
        try:
            return make_site_database(self._bench_root, site)
        except (FileNotFoundError, ValueError, KeyError) as exc:
            raise DatabaseError(f"Site '{site}' not found on this bench.") from exc

    def _database_for(self, site: str) -> str:
        """Resolve a site to the database it owns. Callers pass a site name, never
        a database name - the mapping is a server-side lookup so a client can't
        point diagnostics at an arbitrary database."""
        if not site:
            return ""
        try:
            return site_database_name(self._bench_root, site)
        except (FileNotFoundError, ValueError) as exc:
            raise DatabaseError(f"Site '{site}' not found on this bench.") from exc

    @staticmethod
    def _call(fn, *args):
        """Engines that don't implement an operation raise NotImplementedError
        (see Database's defaults); surface that as a generic, engine-agnostic
        message the UI can key off of, without leaking engine internals."""
        try:
            return fn(*args)
        except NotImplementedError as exc:
            raise DatabaseError(NOT_SUPPORTED) from exc

    @staticmethod
    def _optional(fn, *args):
        """None for a section this engine has no concept of, so one missing
        section does not fail the whole payload. Only for reads the UI can
        omit; a directly requested operation still fails through _call."""
        try:
            return fn(*args)
        except NotImplementedError:
            return None
