"""Tests for DatabaseDiagnosticsProvider's JSON shaping."""

from __future__ import annotations

from unittest.mock import Mock

from admin.backend.providers.database import DatabaseDiagnosticsProvider
from pilot.core.database import BinlogFile, BinlogStatus, LockWaitRow, LockWaitStatus


def _provider(db: Mock) -> DatabaseDiagnosticsProvider:
    return DatabaseDiagnosticsProvider(bench_root=None, database=db)


def test_get_diagnostics_shapes_dataclasses_as_dicts() -> None:
    db = Mock()
    db.get_active_connections.return_value = 3
    db.get_lock_waits.return_value = LockWaitStatus(current_waits=1, total_waits=9, timeout_seconds=50)
    db.get_binlog_status.return_value = BinlogStatus(enabled=True, file_count=2, size_bytes=4096)

    assert _provider(db).get_diagnostics() == {
        "engine": "mariadb",
        "supported": True,
        "active_connections": 3,
        "lock_waits": {"current_waits": 1, "total_waits": 9, "timeout_seconds": 50},
        "binlog": {"enabled": True, "file_count": 2, "size_bytes": 4096},
    }


def test_sqlite_bench_has_no_database_server(tmp_path) -> None:
    import pytest

    from admin.backend.providers.database import NO_DATABASE_SERVER
    from pilot.config import BenchConfig
    from pilot.exceptions import DatabaseError

    (tmp_path / "bench.toml").write_text(
        BenchConfig.from_flat(tmp_path.name, {"db_type": "sqlite"}).dumps()
    )
    provider = DatabaseDiagnosticsProvider(tmp_path)

    assert provider.get_diagnostics() == {
        "engine": "sqlite",
        "supported": False,
        "reason": NO_DATABASE_SERVER,
    }
    # Server-only reads fail loudly rather than pretending the bench has none of each.
    for call in (provider.get_process_list, provider.get_binlog_files):
        with pytest.raises(DatabaseError, match="per-site"):
            call()


def test_get_binlog_files_shapes_files_as_dicts() -> None:
    db = Mock()
    db.get_binlog_files.return_value = [BinlogFile(name="mysql-bin.000001", size_bytes=1024, modified_ms=17)]

    assert _provider(db).get_binlog_files() == [
        {"name": "mysql-bin.000001", "size_bytes": 1024, "modified_ms": 17}
    ]


def test_get_lock_wait_rows_shapes_rows_as_dicts() -> None:
    db = Mock()
    db.get_lock_wait_rows.return_value = [
        LockWaitRow(
            id="42", type="RECORD", mode="X", table="tabDoc", index="PRIMARY",
            state="LOCK WAIT", started="2026-01-01T00:00:00", query="UPDATE tabDoc SET x=1",
            rows_locked=3, rows_modified=1,
        )
    ]

    assert _provider(db).get_lock_wait_rows() == [
        {
            "id": "42", "type": "RECORD", "mode": "X", "table": "tabDoc", "index": "PRIMARY",
            "state": "LOCK WAIT", "started": "2026-01-01T00:00:00", "query": "UPDATE tabDoc SET x=1",
            "rows_locked": 3, "rows_modified": 1,
        }
    ]


def test_purge_binlogs_delegates() -> None:
    db = Mock()
    _provider(db).purge_binlogs("mysql-bin.000002")
    db.purge_binlogs.assert_called_once_with("mysql-bin.000002")


def test_site_filter_resolves_to_the_sites_own_database_name(tmp_path) -> None:
    import json

    db = Mock()
    db.get_process_list.return_value = []
    site = tmp_path / "sites" / "shop.local"
    site.mkdir(parents=True)
    (site / "site_config.json").write_text(json.dumps({"db_name": "_8703c0ab425e4c70"}))

    provider = DatabaseDiagnosticsProvider(bench_root=tmp_path, database=db)
    provider.get_process_list("shop.local")

    # The client names a site; the database name is looked up server-side.
    db.get_process_list.assert_called_once_with("_8703c0ab425e4c70")


def test_site_filter_rejects_unknown_site(tmp_path) -> None:
    import pytest

    from pilot.exceptions import DatabaseError

    provider = DatabaseDiagnosticsProvider(bench_root=tmp_path, database=Mock())
    with pytest.raises(DatabaseError, match="not found"):
        provider.get_process_list("../../etc")


def test_no_site_filter_queries_the_whole_server() -> None:
    db = Mock()
    db.get_lock_wait_rows.return_value = []
    _provider(db).get_lock_wait_rows()
    db.get_lock_wait_rows.assert_called_once_with("")


def test_get_database_size_uses_a_connection_bound_to_the_site(tmp_path) -> None:
    from unittest.mock import patch

    from pilot.core.database import DatabaseSize

    site_db = Mock()
    site_db.get_database_size.return_value = DatabaseSize(
        data_bytes=21, index_bytes=27, claimable_bytes=4, free_bytes=99
    )
    provider = DatabaseDiagnosticsProvider(bench_root=tmp_path, database=Mock())

    with patch("admin.backend.providers.database.make_site_database", return_value=site_db) as make:
        assert provider.get_database_size("shop.local") == {
            "data_bytes": 21,
            "index_bytes": 27,
            "claimable_bytes": 4,
            "free_bytes": 99,
        }

    make.assert_called_once_with(tmp_path, "shop.local")


def test_get_database_size_without_a_site_uses_the_server_connection() -> None:
    from pilot.core.database import DatabaseSize

    db = Mock()
    db.get_database_size.return_value = DatabaseSize(
        data_bytes=1, index_bytes=2, claimable_bytes=None, free_bytes=None
    )
    assert _provider(db).get_database_size()["data_bytes"] == 1
    db.get_database_size.assert_called_once_with()


def test_get_table_sizes_requires_a_site() -> None:
    import pytest

    from pilot.exceptions import DatabaseError

    with pytest.raises(DatabaseError, match="site is required"):
        _provider(Mock()).get_table_sizes("")


def test_unsupported_operation_surfaces_generic_message() -> None:
    import pytest

    from admin.backend.providers.database import NOT_SUPPORTED
    from pilot.exceptions import DatabaseError

    db = Mock()
    db.get_binlog_files.side_effect = NotImplementedError
    with pytest.raises(DatabaseError, match=NOT_SUPPORTED):
        _provider(db).get_binlog_files()
