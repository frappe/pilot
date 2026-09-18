"""Tests for DatabaseDiagnosticsProvider's JSON shaping."""

from __future__ import annotations

from unittest.mock import Mock, PropertyMock

from admin.backend.providers.database import DatabaseDiagnosticsProvider
from pilot.core.database import BinlogFile, BinlogStatus, DatabaseProcess, LockWaitRow, LockWaitStatus


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
        "performance_schema_enabled": db.is_performance_schema_enabled,
    }


def test_get_process_list_shapes_processes_as_dicts() -> None:
    db = Mock()
    db.get_process_list.return_value = [
        DatabaseProcess(
            id=7,
            user="app",
            host="localhost:53422",
            database="mydb",
            command="Query",
            state=None,
            duration_seconds=3.0,
            query="SELECT 1",
        )
    ]

    assert _provider(db).get_process_list() == [
        {
            "id": 7,
            "user": "app",
            "host": "localhost:53422",
            "database": "mydb",
            "command": "Query",
            "state": None,
            "duration_seconds": 3.0,
            "query": "SELECT 1",
        }
    ]


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


def test_get_diagnostics_reports_no_binlog_for_an_engine_without_one() -> None:
    """PostgreSQL has no binary log. The section goes null instead of failing
    the whole payload, which used to leave the page with no engine at all."""
    db = Mock()
    db.get_active_connections.return_value = 3
    db.get_lock_waits.return_value = LockWaitStatus(current_waits=0, total_waits=None, timeout_seconds=None)
    db.get_binlog_status.side_effect = NotImplementedError
    type(db).is_performance_schema_enabled = PropertyMock(side_effect=NotImplementedError)
    provider = DatabaseDiagnosticsProvider(bench_root=None, database=db, engine="postgres")

    assert provider.get_diagnostics() == {
        "engine": "postgres",
        "supported": True,
        "active_connections": 3,
        "lock_waits": {"current_waits": 0, "total_waits": None, "timeout_seconds": None},
        "binlog": None,
        "performance_schema_enabled": False,
    }


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
            "total_bytes": None,
        }

    make.assert_called_once_with(tmp_path, "shop.local")


def test_site_scoped_size_takes_free_space_from_the_server(tmp_path) -> None:
    """PostgreSQL hides the data directory from non-superusers, but the free
    space it measures is the same disk whatever the scope."""
    from unittest.mock import patch

    from pilot.core.database import DatabaseSize

    site_db = Mock()
    site_db.get_database_size.return_value = DatabaseSize(
        data_bytes=21, index_bytes=27, claimable_bytes=None, free_bytes=None
    )
    server = Mock()
    server.get_free_disk_bytes.return_value = 855949434880
    provider = DatabaseDiagnosticsProvider(bench_root=tmp_path, database=server)

    with patch("admin.backend.providers.database.make_site_database", return_value=site_db):
        assert provider.get_database_size("shop.local")["free_bytes"] == 855949434880


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


def _section():
    from pilot.core.database import PerformanceSection

    return PerformanceSection(data=[], has_next_page=True)


def test_a_performance_section_resolves_the_site_to_its_database() -> None:
    from unittest.mock import patch

    db = Mock()
    db.get_unused_indexes.return_value = _section()
    provider = DatabaseDiagnosticsProvider(bench_root=None, database=db)

    with patch("admin.backend.providers.database.site_database_name", return_value="_abc123") as resolve:
        result = provider.get_unused_indexes("shop.local", 50, 100)

    resolve.assert_called_once_with(None, "shop.local")
    db.get_unused_indexes.assert_called_once_with("_abc123", 50, 100)
    assert result == {"data": [], "has_next_page": True}


def test_a_performance_section_without_a_site_covers_the_server() -> None:
    db = Mock()
    db.get_redundant_indexes.return_value = _section()

    DatabaseDiagnosticsProvider(bench_root=None, database=db).get_redundant_indexes()

    db.get_redundant_indexes.assert_called_once_with("", 20, 0)


def test_a_performance_section_maps_unsupported_engine() -> None:
    import pytest

    from pilot.exceptions import DatabaseError

    db = Mock()
    db.get_time_consuming_queries.side_effect = NotImplementedError

    with pytest.raises(DatabaseError):
        DatabaseDiagnosticsProvider(bench_root=None, database=db).get_time_consuming_queries()
