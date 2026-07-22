from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from pilot.config import BenchConfig
from pilot.core.database import Database, make_database


class DatabaseDiagnosticsProvider:
    """Server-level diagnostics for the bench's database, shaped for JSON.

    Timestamps stay raw (epoch ms) and sizes stay raw bytes; formatting
    belongs to the consumer.
    """

    def __init__(self, bench_root: Path, database: Database | None = None) -> None:
        self._db = database or make_database(BenchConfig.read(bench_root, validate=False))

    def get_diagnostics(self) -> dict:
        return {
            "active_connections": self._db.get_active_connections(),
            "lock_waits": asdict(self._db.get_lock_waits()),
            "binlog": asdict(self._db.get_binlog_status()),
        }

    def get_process_list(self) -> list[dict]:
        return self._db.get_process_list()

    def get_binlog_files(self) -> list[dict]:
        return [asdict(file) for file in self._db.get_binlog_files()]

    def purge_binlogs(self, up_to: str) -> None:
        self._db.purge_binlogs(up_to)
