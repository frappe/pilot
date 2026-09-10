from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from pilot.exceptions import DatabaseError


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    duration_ms: float
    truncated: bool = False
    affected_rows: int = 0


@dataclass
class DatabaseProcess:
    """One client connection. `command` is what it is doing (MariaDB's Command,
    PostgreSQL's state), `state` the finer step within it, and
    `duration_seconds` the time spent in that state."""

    id: int
    user: str | None
    host: str | None
    database: str | None
    command: str | None
    state: str | None
    duration_seconds: float | None
    query: str | None


@dataclass
class LockWaitStatus:
    current_waits: int
    total_waits: int | None
    timeout_seconds: int | None


@dataclass
class LockWaitRow:
    """One waiting lock request. `table`/`index`/`rows_locked`/`rows_modified`
    are None where an engine has no equivalent (e.g. PostgreSQL tracks
    neither an index name nor per-transaction row counts)."""

    id: str
    type: str
    mode: str
    table: str | None
    index: str | None
    state: str | None
    started: str | None
    query: str | None
    rows_locked: int | None
    rows_modified: int | None


@dataclass
class DatabaseSize:
    """Storage breakdown. `total_bytes` carries an exact combined size when an
    engine cannot split a server-wide total into data and indexes.
    `claimable_bytes` is space a rebuild would return to the filesystem;
    `free_bytes` is what the data directory's disk has left. Fields stay None
    when the engine or a remote host cannot report them."""

    data_bytes: int | None
    index_bytes: int | None
    claimable_bytes: int | None
    free_bytes: int | None
    total_bytes: int | None = None


@dataclass
class TableSize:
    name: str
    data_bytes: int
    index_bytes: int
    claimable_bytes: int | None


@dataclass
class BinlogStatus:
    enabled: bool
    file_count: int
    size_bytes: int


@dataclass
class BinlogFile:
    name: str
    size_bytes: int
    modified_ms: int | None  # None when the binlog directory is remote or unreadable


@dataclass
class StorageComponent:
    """One on-disk artifact the engine keeps alongside the databases. `key`
    identifies it across engines; `label` names it in the engine's own terms."""

    key: str
    label: str
    bytes: int


@dataclass
class TimeConsumingQuery:
    """A normalized statement ranked by the total time spent running it.
    `percent` is its share of the scope's total execution time."""

    database: str | None
    query: str | None
    percent: float
    calls: int
    average_time_ms: float
    total_time_ms: float


@dataclass
class FullTableScanQuery:
    """A normalized statement that ran without a usable index."""

    database: str | None
    query: str | None
    calls: int
    rows_sent: int
    rows_examined: int


@dataclass
class UnusedIndex:
    database: str | None
    table: str
    index: str


@dataclass
class RedundantIndex:
    """An index whose columns are already covered by `dominant_index`."""

    database: str | None
    table: str
    redundant_index: str
    redundant_index_columns: str
    dominant_index: str
    dominant_index_columns: str


@dataclass
class PerformanceSection:
    """One page of one performance report section. `has_next_page` comes from
    reading one row past the page, so it costs no extra query."""

    data: list[TimeConsumingQuery | FullTableScanQuery | UnusedIndex | RedundantIndex]
    has_next_page: bool


class Database(ABC):
    @abstractmethod
    def execute(self, query: str, read_only: bool = True) -> QueryResult: ...

    def quote_identifier(self, name: str) -> str:
        """ANSI double quoting, which PostgreSQL and SQLite both accept.
        MariaDB overrides it with backticks."""
        return '"{}"'.format(name.replace('"', ""))

    @abstractmethod
    def get_tables(self) -> list[str]: ...

    @abstractmethod
    def get_table_columns(self, table: str) -> list[dict]: ...

    def get_schema(self) -> list[dict]:
        return [{"name": t, "columns": self.get_table_columns(t)} for t in self.get_tables()]

    def get_process_list(self, database: str = "") -> list[DatabaseProcess]:
        """`database` narrows the result to one database; empty means server-wide."""
        raise NotImplementedError

    def get_database_size(self) -> DatabaseSize:
        """Sizes for whatever this connection covers: one database when it is
        bound to one, otherwise the whole server."""
        raise NotImplementedError

    def get_table_sizes(self) -> list[TableSize]:
        """Per-table sizes for this connection's database, largest first."""
        raise NotImplementedError

    def kill_process(self, process_id: int) -> None:
        raise NotImplementedError

    def get_active_connections(self) -> int:
        raise NotImplementedError

    def get_lock_waits(self) -> LockWaitStatus:
        raise NotImplementedError

    def get_lock_wait_rows(self, database: str = "") -> list[LockWaitRow]:
        """`database` narrows the result to one database; empty means server-wide."""
        raise NotImplementedError

    def get_time_consuming_queries(
        self, database: str = "", limit: int = 20, offset: int = 0
    ) -> PerformanceSection:
        """`database` narrows the result to one database; empty means server-wide."""
        raise NotImplementedError

    def get_full_table_scan_queries(
        self, database: str = "", limit: int = 20, offset: int = 0
    ) -> PerformanceSection:
        """`database` narrows the result to one database; empty means server-wide."""
        raise NotImplementedError

    def get_unused_indexes(
        self, database: str = "", limit: int = 20, offset: int = 0
    ) -> PerformanceSection:
        """`database` narrows the result to one database; empty means server-wide."""
        raise NotImplementedError

    def get_redundant_indexes(
        self, database: str = "", limit: int = 20, offset: int = 0
    ) -> PerformanceSection:
        """`database` narrows the result to one database; empty means server-wide."""
        raise NotImplementedError

    @property
    def is_performance_schema_enabled(self) -> bool:
        raise NotImplementedError

    def get_binlog_status(self) -> BinlogStatus:
        raise NotImplementedError

    def get_binlog_files(self) -> list[BinlogFile]:
        raise NotImplementedError

    def purge_binlogs(self, up_to: str) -> None:
        raise NotImplementedError

    def get_storage_components(self) -> list[StorageComponent]:
        """Sizes of the engine's own files, excluding the databases."""
        raise NotImplementedError

    def get_schema_sizes(self) -> dict[str, int]:
        """Bytes every database on this server holds on disk, keyed by database
        name. One round trip for the whole server, system schemas included.

        Allocated-but-unused space counts: it is space the disk cannot hand to
        anything else until the database is rebuilt."""
        raise NotImplementedError

    def get_data_directory(self) -> str | None:
        """Server data directory, or None when the server is not on this host
        and the path would therefore be meaningless locally."""
        raise NotImplementedError

    def get_free_disk_bytes(self) -> int | None:
        """Free space on the disk holding the data directory, or None when that
        directory cannot be reached from here."""
        import shutil

        directory = self.get_data_directory()
        if not directory:
            return None
        try:
            return shutil.disk_usage(directory).free
        except OSError:
            return None

    def get_scalar(self, query: str):
        result = self.execute(query)
        if not result.rows:
            raise DatabaseError(f"Query returned no rows: {query}")
        return result.rows[0][0]
