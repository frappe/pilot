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

    @abstractmethod
    def get_process_list(self) -> list[dict]: ...

    @abstractmethod
    def kill_process(self, process_id: int) -> None: ...

    @abstractmethod
    def get_active_connections(self) -> int: ...

    @abstractmethod
    def get_lock_waits(self) -> LockWaitStatus: ...

    @abstractmethod
    def get_binlog_status(self) -> BinlogStatus: ...

    @abstractmethod
    def get_binlog_files(self) -> list[BinlogFile]: ...

    @abstractmethod
    def purge_binlogs(self, up_to: str) -> None: ...
