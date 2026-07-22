"""Tests for DatabaseDiagnosticsProvider's JSON shaping."""

from __future__ import annotations

from unittest.mock import Mock

from admin.backend.providers.database import DatabaseDiagnosticsProvider
from pilot.core.database import BinlogFile, BinlogStatus, LockWaitStatus


def _provider(db: Mock) -> DatabaseDiagnosticsProvider:
    return DatabaseDiagnosticsProvider(bench_root=None, database=db)


def test_get_diagnostics_shapes_dataclasses_as_dicts() -> None:
    db = Mock()
    db.get_active_connections.return_value = 3
    db.get_lock_waits.return_value = LockWaitStatus(current_waits=1, total_waits=9, timeout_seconds=50)
    db.get_binlog_status.return_value = BinlogStatus(enabled=True, file_count=2, size_bytes=4096)

    assert _provider(db).get_diagnostics() == {
        "active_connections": 3,
        "lock_waits": {"current_waits": 1, "total_waits": 9, "timeout_seconds": 50},
        "binlog": {"enabled": True, "file_count": 2, "size_bytes": 4096},
    }


def test_get_binlog_files_shapes_files_as_dicts() -> None:
    db = Mock()
    db.get_binlog_files.return_value = [BinlogFile(name="mysql-bin.000001", size_bytes=1024, modified_ms=17)]

    assert _provider(db).get_binlog_files() == [
        {"name": "mysql-bin.000001", "size_bytes": 1024, "modified_ms": 17}
    ]


def test_purge_binlogs_delegates() -> None:
    db = Mock()
    _provider(db).purge_binlogs("mysql-bin.000002")
    db.purge_binlogs.assert_called_once_with("mysql-bin.000002")
