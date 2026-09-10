"""Tests for StorageProvider's disk usage breakdown."""

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


def _make_app(bench_root: Path, name: str, content: bytes = b"x" * 1024) -> None:
    app_dir = bench_root / "apps" / name
    app_dir.mkdir(parents=True)
    (app_dir / ".git").mkdir()
    (app_dir / "payload.bin").write_bytes(content)


def _make_site(bench_root: Path, name: str, db_name: str, content: bytes = b"x" * 2048) -> None:
    site_dir = bench_root / "sites" / name
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(f'{{"db_name": "{db_name}"}}')
    (site_dir / "payload.bin").write_bytes(content)


def test_bench_breakdown_sums_apps_sites_and_logs(tmp_path: Path) -> None:
    from admin.backend.providers.storage import StorageProvider

    _write_bench(tmp_path)
    _make_app(tmp_path, "frappe", b"a" * 4096)
    _make_site(tmp_path, "site1.local", "site1_db", b"b" * 8192)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "web.log").write_bytes(b"c" * 512)

    breakdown = StorageProvider(tmp_path)._bench_breakdown()

    assert [entry.name for entry in breakdown.apps] == ["frappe"]
    assert [entry.name for entry in breakdown.sites] == ["site1.local"]
    assert breakdown.apps_bytes > 0
    assert breakdown.sites_bytes > 0
    assert breakdown.logs_bytes > 0
    assert breakdown.used_bytes == (breakdown.apps_bytes + breakdown.sites_bytes + breakdown.logs_bytes)


def test_site_storage_splits_private_public_and_backups(tmp_path: Path) -> None:
    from admin.backend.providers.storage import StorageProvider

    _write_bench(tmp_path)
    site_dir = tmp_path / "sites" / "site1.local"
    (site_dir / "private" / "files").mkdir(parents=True)
    (site_dir / "private" / "files" / "a.pdf").write_bytes(b"x" * 1000)
    (site_dir / "private" / "backups").mkdir(parents=True)
    (site_dir / "private" / "backups" / "b.sql.gz").write_bytes(b"x" * 2000)
    (site_dir / "public" / "files").mkdir(parents=True)
    (site_dir / "public" / "files" / "c.jpg").write_bytes(b"x" * 500)
    (site_dir / "logs").mkdir()
    (site_dir / "logs" / "d.log").write_bytes(b"x" * 250)
    (site_dir / "site_config.json").write_text('{"db_name": "site1_db"}')

    site = StorageProvider(tmp_path)._bench.site("site1.local")
    storage = StorageProvider(tmp_path)._site_storage(site)

    assert storage.name == "site1.local"
    assert storage.private_files_bytes >= 1000
    assert storage.backups_bytes >= 2000
    assert storage.public_files_bytes >= 500
    assert storage.other_bytes >= 250
    assert storage.bytes == (
        storage.private_files_bytes
        + storage.public_files_bytes
        + storage.backups_bytes
        + storage.other_bytes
    )
    assert {entry.name: entry.bytes for entry in storage.backup_files} == {"b.sql.gz": 2000}
    other_names = {entry.name for entry in storage.other_entries}
    assert other_names == {"logs", "site_config.json"}
    assert "files" not in other_names
    assert "backups" not in other_names


def _mock_database(
    schema_sizes: dict[str, int], components: list[StorageComponent], data_dir: str | None
) -> Mock:
    db = Mock()
    db.get_schema_sizes.return_value = schema_sizes
    db.get_storage_components.return_value = components
    db.get_data_directory.return_value = data_dir
    return db


def test_mariadb_breakdown_shapes_schemas_and_reconciles_core(tmp_path: Path) -> None:
    from admin.backend.providers.storage import DatabaseBreakdown, DatabaseRow, StorageProvider

    _write_bench(tmp_path)
    _make_site(tmp_path, "site1.local", "site1_db")
    components = [
        StorageComponent("binlog", "binary log", 50),
        StorageComponent("error_log", "error log", 20),
        StorageComponent("slow_log", "slow query log", 0),
        StorageComponent("binlog_index", "binary log index", 0),
    ]
    db = _mock_database({"site1_db": 500, "mysql": 100}, components, str(tmp_path))

    with (
        patch("admin.backend.providers.storage.make_database", return_value=db),
        patch("admin.backend.providers.storage.directory_size_bytes", return_value=1000),
    ):
        breakdown = StorageProvider(tmp_path)._database_breakdown()

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
    from admin.backend.providers.storage import StorageProvider

    _write_bench(tmp_path, db_type="postgres")
    _make_site(tmp_path, "site1.local", "site1_db")
    components = [
        StorageComponent("wal", "write-ahead log", 300),
        StorageComponent("server_log", "server log", 100),
    ]
    db = _mock_database({"site1_db": 500, "postgres": 60}, components, "/pg/data")

    with (
        patch("admin.backend.providers.storage.make_database", return_value=db),
        patch("admin.backend.providers.storage.directory_size_bytes", return_value=2000),
    ):
        breakdown = StorageProvider(tmp_path)._database_breakdown()

    assert breakdown.engine == "postgres"
    assert breakdown.supported is True
    assert breakdown.used_bytes == 2000
    assert breakdown.binlog_bytes == 0
    assert breakdown.core_bytes == 1040  # 2000 - (500 + 60) - (300 + 100)
    assert breakdown.components == components
    assert [row.site for row in breakdown.databases] == ["site1.local", None]


def test_core_bytes_is_unknown_when_the_data_directory_cannot_be_read(tmp_path: Path) -> None:
    """An unreadable data directory is not an empty one - core_bytes goes None
    and used_bytes falls back to what was actually measured."""
    from admin.backend.providers.storage import StorageProvider
    from pilot.exceptions import CommandError

    _write_bench(tmp_path, db_type="postgres")
    components = [StorageComponent("wal", "write-ahead log", 300)]
    db = _mock_database({"postgres": 60}, components, "/pg/data")

    with (
        patch("admin.backend.providers.storage.make_database", return_value=db),
        patch(
            "admin.backend.providers.storage.directory_size_bytes",
            side_effect=CommandError("Permission denied"),
        ),
    ):
        breakdown = StorageProvider(tmp_path)._database_breakdown()

    assert breakdown.core_bytes is None
    assert breakdown.used_bytes == 360


def test_core_bytes_is_unknown_for_a_remote_database_server(tmp_path: Path) -> None:
    from admin.backend.providers.storage import StorageProvider

    _write_bench(tmp_path)
    db = _mock_database({"site1_db": 500}, [StorageComponent("binlog", "binary log", 50)], None)

    with patch("admin.backend.providers.storage.make_database", return_value=db):
        breakdown = StorageProvider(tmp_path)._database_breakdown()

    assert breakdown.core_bytes is None
    assert breakdown.used_bytes == 550


def test_sqlite_engine_is_not_supported(tmp_path: Path) -> None:
    from admin.backend.providers.storage import DatabaseBreakdown, StorageProvider

    _write_bench(tmp_path, db_type="sqlite")

    breakdown = StorageProvider(tmp_path)._database_breakdown()

    assert breakdown == DatabaseBreakdown(
        engine="sqlite", supported=False, used_bytes=0, binlog_bytes=0
    )


def test_get_breakdown_remeasures_instead_of_serving_the_cache(tmp_path: Path) -> None:
    """directory_size_bytes is cached for the /metrics poll and never expires,
    so a refresh would otherwise report the first reading forever."""
    from admin.backend.providers.storage import StorageProvider

    _write_bench(tmp_path)
    _make_app(tmp_path, "frappe", b"a" * 4096)
    db = _mock_database({"site1_db": 500}, [], str(tmp_path))

    with patch("admin.backend.providers.storage.make_database", return_value=db):
        provider = StorageProvider(tmp_path)
        first = provider.get_breakdown(1000, 500).bench.apps_bytes
        (tmp_path / "apps" / "frappe" / "grew.bin").write_bytes(b"a" * 200_000)
        second = provider.get_breakdown(1000, 500).bench.apps_bytes

    assert second > first


def test_directory_size_bytes_measures_a_real_tree(tmp_path: Path) -> None:
    """Guards the du flags: GNU-only `-b` fails outright on BSD/macOS, which
    used to be swallowed into a silent 0."""
    from admin.backend.providers.storage import directory_size_bytes

    (tmp_path / "payload.bin").write_bytes(b"x" * 40960)

    assert directory_size_bytes(str(tmp_path)) >= 40960
    assert directory_size_bytes(str(tmp_path / "missing")) == 0
