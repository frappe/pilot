from __future__ import annotations

from datetime import datetime
from itertools import pairwise
from pathlib import Path

from admin.backend.providers.windowed_log import WindowedLogProvider

MAX_POINTS = 400


class DatabaseMonitoringProvider(WindowedLogProvider):
    """Time-series for a window from the raw MariaDB status log. Each point is a
    delta between consecutive daemon samples, mirroring the system net/disk rates."""

    def __init__(self, bench_root: Path, window: str) -> None:
        super().__init__(window)
        self._bench_root = bench_root

    def get_history(self) -> dict:
        """The database metrics history."""
        from admin.backend.providers.database.slow_queries import SlowQueryProvider
        from pilot.config.monitor import db_log_path

        path = db_log_path()
        rows = self._rows(path)
        return {
            "window": self.window,
            "window_seconds": self.window_seconds,
            "now": self.now_ms(),
            "earliest": self.get_earliest(path),
            "points": [self._point(prev, cur) for prev, cur in pairwise(rows)],
            "slow_queries": SlowQueryProvider(self._bench_root, self.window).get_overview(),
        }

    def _rows(self, path: Path) -> list[tuple[datetime, dict]]:
        """The records in the window, with their times."""
        rows = [
            (when, record)
            for record in self.records_in_window(path)
            if (when := self.get_time(record.get("time"))) is not None
        ]
        rows.reverse()
        if len(rows) <= MAX_POINTS:
            return rows
        step = len(rows) // MAX_POINTS + 1
        return rows[::step]

    def _point(self, prev: tuple[datetime, dict], cur: tuple[datetime, dict]) -> dict:
        """One chart point from two records."""
        (_, before), (t1, after) = prev, cur
        delta = lambda key: max(after.get(key, 0) - before.get(key, 0), 0)  # noqa: E731

        by_type = {key: delta(key) for key in ("Com_insert", "Com_update", "Com_delete", "Com_select")}
        other = max(delta("Questions") - sum(by_type.values()), 0)
        read_requests = delta("Innodb_buffer_pool_read_requests")
        reads = delta("Innodb_buffer_pool_reads")
        lock_waits = delta("Innodb_row_lock_waits")
        pool_size = after.get("innodb_buffer_pool_size", 0)
        total_ram = after.get("total_ram_bytes") or 0
        return {
            "time": self.to_epoch_ms(t1),
            "Insert": by_type["Com_insert"],
            "Update": by_type["Com_update"],
            "Delete": by_type["Com_delete"],
            "Select": by_type["Com_select"],
            "Other": other,
            "Buffer Pool Miss %": round(reads / read_requests * 100, 4) if read_requests else 0.0,
            "Avg Row Lock Wait": round(delta("Innodb_row_lock_time") / lock_waits, 2) if lock_waits else 0.0,
            "Connected": after.get("Threads_connected", 0),
            "Max Connections": after.get("max_connections", 0),
            "Buffer Pool Size": pool_size,
            "Buffer Pool % RAM": round(pool_size / total_ram * 100, 2) if total_ram else 0.0,
        }
