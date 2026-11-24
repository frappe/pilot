"""Tests for StorageProvider: the database breakdown, and the bench report it serves."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from pilot.config import BenchConfig
from pilot.core.database.base import StorageComponent


def _write_bench(bench_root: Path, db_type: str = "mariadb") -> None:
    bench_root.mkdir(parents=True, exist_ok=True)
    (bench_root / "bench.toml").write_text(
        BenchConfig.from_flat(bench_root.name, {"db_type": db_type}).dumps()
    )


def _make_site(bench_root: Path, name: str, db_name: str, content: bytes = b"x" * 2048) -> None:
    site_dir = bench_root / "sites" / name
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(f'{{"db_name": "{db_name}"}}')
    (site_dir / "payload.bin").write_bytes(content)


def _mock_database(
    schema_sizes: dict[str, int], components: list[StorageComponent], data_dir: str | None
) -> Mock:
    db = Mock()
    db.get_schema_sizes.return_value = schema_sizes
    db.get_storage_components.return_value = components
    db.get_data_directory.return_value = data_dir
    return db


def test_mariadb_breakdown_shapes_schemas_and_reconciles_core(tmp_path: Path) -> None:
    from admin.backend.providers.server.storage import DatabaseBreakdown, DatabaseRow, StorageProvider

    _write_bench(tmp_path)
    _make_site(tmp_path, "site1.local", "site1_db")
    components = [
        StorageComponent("binlog", "binary log", 50),
        StorageComponent("error_log", "error log", 20),
        StorageComponent("slow_log", "slow query log", 0),
        StorageComponent("binlog_index", "binary log index", 0),
    ]
    db = _mock_database({"site1_db": 500, "mysql": 100}, components, str(tmp_path))

    with patch("admin.backend.providers.server.storage.make_database", return_value=db):
        breakdown = StorageProvider(tmp_path)._database_breakdown(1000)

    assert breakdown == DatabaseBreakdown(
        engine="mariadb",
        supported=True,
        used_bytes=1000,
        binlog_bytes=50,
        core_bytes=330,  # 1000 - (500 + 100) - (50 + 20)
        components=components,
        databases=[
            DatabaseRow(schema="site1_db", site="site1.local", system=False, bytes=500),
            DatabaseRow(schema="mysql", site=None, system=True, bytes=100),
        ],
    )


def test_postgres_breakdown_reports_wal_and_server_log(tmp_path: Path) -> None:
    """The engine names its own artifacts; postgres has no binlog, so
    binlog_bytes stays 0 and the purge alert has nothing to act on."""
    from admin.backend.providers.server.storage import StorageProvider

    _write_bench(tmp_path, db_type="postgres")
    _make_site(tmp_path, "site1.local", "site1_db")
    components = [
        StorageComponent("wal", "write-ahead log", 300),
        StorageComponent("server_log", "server log", 100),
    ]
    db = _mock_database({"site1_db": 500, "postgres": 60}, components, "/pg/data")

    with patch("admin.backend.providers.server.storage.make_database", return_value=db):
        breakdown = StorageProvider(tmp_path)._database_breakdown(2000)

    assert breakdown.engine == "postgres"
    assert breakdown.supported is True
    assert breakdown.used_bytes == 2000
    assert breakdown.binlog_bytes == 0
    assert breakdown.core_bytes == 1040  # 2000 - (500 + 60) - (300 + 100)
    assert breakdown.components == components
    assert [row.site for row in breakdown.databases] == ["site1.local", None]


def test_core_bytes_is_unknown_without_a_measured_data_directory(tmp_path: Path) -> None:
    from admin.backend.providers.server.storage import StorageProvider

    _write_bench(tmp_path)
    db = _mock_database({"site1_db": 500}, [StorageComponent("binlog", "binary log", 50)], None)

    with patch("admin.backend.providers.server.storage.make_database", return_value=db):
        breakdown = StorageProvider(tmp_path)._database_breakdown(None)

    assert breakdown.core_bytes is None
    assert breakdown.used_bytes == 550


def test_sqlite_engine_is_not_supported(tmp_path: Path) -> None:
    from admin.backend.providers.server.storage import DatabaseBreakdown, StorageProvider

    _write_bench(tmp_path, db_type="sqlite")

    breakdown = StorageProvider(tmp_path)._database_breakdown(None)

    assert breakdown == DatabaseBreakdown(
        engine="sqlite", supported=False, used_bytes=0, binlog_bytes=0
    )


def _provider_without_database(tmp_path: Path):
    from admin.backend.providers.server.storage import DatabaseBreakdown, StorageProvider

    _write_bench(tmp_path)
    provider = StorageProvider(tmp_path)
    provider._database_breakdown = Mock(
        return_value=DatabaseBreakdown(engine="mariadb", supported=False, used_bytes=0, binlog_bytes=0)
    )
    return provider


def test_get_breakdown_serves_the_report_without_queuing_by_default(tmp_path: Path) -> None:
    from admin.backend.providers.server.storage import RefreshBenchStorageTask

    provider = _provider_without_database(tmp_path)

    with patch.object(RefreshBenchStorageTask, "queue") as queue:
        breakdown = provider.get_breakdown(1000, 500)

    assert breakdown.bench is None
    assert breakdown.bench_task is None
    queue.assert_not_called()


def test_get_breakdown_queues_a_refresh_for_a_missing_report_when_asked(tmp_path: Path) -> None:
    from admin.backend.providers.server.storage import BenchTask, RefreshBenchStorageTask

    provider = _provider_without_database(tmp_path)

    with (
        patch.object(RefreshBenchStorageTask, "queue", return_value="task-1") as queue,
        patch.object(type(provider), "get_bench_task", side_effect=lambda task_id: BenchTask(task_id, "queued")),
    ):
        breakdown = provider.get_breakdown(1000, 500, refresh_if_stale=True)

    assert breakdown.bench_task == BenchTask("task-1", "queued")
    queue.assert_called_once()


def test_get_breakdown_does_not_queue_for_a_fresh_report(tmp_path: Path) -> None:
    from admin.backend.providers.server.storage import RefreshBenchStorageTask

    provider = _provider_without_database(tmp_path)
    with patch.object(type(provider._bench.storage), "get_database_directory_bytes", return_value=4096):
        provider._bench.storage.collect()

    with patch.object(RefreshBenchStorageTask, "queue") as queue:
        breakdown = provider.get_breakdown(1000, 500, refresh_if_stale=True)

    assert breakdown.bench is not None
    assert breakdown.bench_task is None
    provider._database_breakdown.assert_called_once_with(4096)
    queue.assert_not_called()


def test_refresh_bench_returns_the_measure_already_running(tmp_path: Path) -> None:
    from admin.backend.providers.server.storage import RefreshBenchStorageTask
    from pilot.exceptions import TaskConflictError

    provider = _provider_without_database(tmp_path)

    with (
        patch.object(RefreshBenchStorageTask, "queue", side_effect=TaskConflictError("busy")),
        patch.object(type(provider), "get_bench_task_id", return_value="task-running"),
    ):
        assert provider.refresh_bench() == "task-running"


def test_refresh_bench_queues_again_when_the_running_measure_just_ended(tmp_path: Path) -> None:
    from admin.backend.providers.server.storage import RefreshBenchStorageTask
    from pilot.exceptions import TaskConflictError

    provider = _provider_without_database(tmp_path)

    with (
        patch.object(RefreshBenchStorageTask, "queue", side_effect=[TaskConflictError("busy"), "task-new"]),
        patch.object(type(provider), "get_bench_task_id", return_value=None),
    ):
        assert provider.refresh_bench() == "task-new"
