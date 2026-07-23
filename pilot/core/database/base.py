from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    duration_ms: float
    truncated: bool = False
    affected_rows: int = 0


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
    """Storage breakdown. `claimable_bytes` is space a rebuild would return to
    the filesystem; `free_bytes` is what the data directory's disk has left.
    Either is None when the engine or a remote host can't report it."""

    data_bytes: int
    index_bytes: int
    claimable_bytes: int | None
    free_bytes: int | None


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


class Database(ABC):
    @abstractmethod
    def execute(self, query: str, read_only: bool = True) -> QueryResult: ...

    @abstractmethod
    def get_tables(self) -> list[str]: ...

    @abstractmethod
    def get_table_columns(self, table: str) -> list[dict]: ...

    def get_schema(self) -> list[dict]:
        return [{"name": t, "columns": self.get_table_columns(t)} for t in self.get_tables()]

    def get_process_list(self, database: str = "") -> list[dict]:
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

    def get_binlog_status(self) -> BinlogStatus:
        raise NotImplementedError

    def get_binlog_files(self) -> list[BinlogFile]:
        raise NotImplementedError

    def purge_binlogs(self, up_to: str) -> None:
        raise NotImplementedError
