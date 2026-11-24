from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.core.bench.storage import BenchStorage

measure_database_directory = BenchStorage.get_database_directory_bytes


@pytest.fixture(autouse=True)
def no_database_server():
    """The folder tests need no database server."""
    with patch.object(BenchStorage, "get_database_directory_bytes", return_value=None):
        yield


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
    _write_bench(tmp_path)
    _make_app(tmp_path, "frappe", b"a" * 4096)
    _make_site(tmp_path, "site1.local", "site1_db", b"b" * 8192)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "web.log").write_bytes(b"c" * 512)

    (tmp_path / "env" / "lib").mkdir(parents=True)
    (tmp_path / "env" / "lib" / "site.py").write_bytes(b"d" * 4096)

    breakdown = Bench(tmp_path).storage.measure()

    assert [entry.name for entry in breakdown.apps] == ["frappe"]
    assert [entry.name for entry in breakdown.sites] == ["site1.local"]
    assert breakdown.apps_bytes > 0
    assert breakdown.env_bytes > 0
    assert breakdown.sites_bytes > 0
    assert breakdown.logs_bytes > 0
    assert [item.name for item in breakdown.others] == ["bench.toml"]
    parts = breakdown.apps_bytes + breakdown.env_bytes + breakdown.sites_bytes + breakdown.logs_bytes
    assert breakdown.used_bytes >= parts + breakdown.others_bytes


def test_app_storage_splits_git_and_node_modules(tmp_path: Path) -> None:
    _write_bench(tmp_path)
    _make_app(tmp_path, "frappe", b"a" * 4096)
    app_dir = tmp_path / "apps" / "frappe"
    (app_dir / ".git" / "pack").write_bytes(b"g" * 8192)
    (app_dir / "node_modules").mkdir()
    (app_dir / "node_modules" / "lib.js").write_bytes(b"n" * 16384)
    (app_dir / "frontend" / "node_modules").mkdir(parents=True)
    (app_dir / "frontend" / "node_modules" / "vue.js").write_bytes(b"v" * 16384)

    app = Bench(tmp_path).storage.measure().apps[0]

    assert app.git_bytes >= 8192
    assert app.node_modules_bytes >= 32768
    assert app.bytes >= app.git_bytes + app.node_modules_bytes + 4096


def test_app_without_node_modules_reports_zero(tmp_path: Path) -> None:
    _write_bench(tmp_path)
    _make_app(tmp_path, "frappe")

    app = Bench(tmp_path).storage.measure().apps[0]

    assert app.node_modules_bytes == 0


def test_site_storage_splits_private_public_and_backups(tmp_path: Path) -> None:
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

    bench = Bench(tmp_path)
    storage = bench.storage.get_site(bench.site("site1.local"))

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


def test_app_counts_nested_node_modules_once(tmp_path: Path) -> None:
    _write_bench(tmp_path)
    _make_app(tmp_path, "frappe")
    inner = tmp_path / "apps" / "frappe" / "node_modules" / "pkg" / "node_modules"
    inner.mkdir(parents=True)
    (inner / "dep.js").write_bytes(b"d" * 65536)

    app = Bench(tmp_path).storage.measure().apps[0]

    assert 65536 <= app.node_modules_bytes < 2 * 65536
    assert app.bytes >= app.node_modules_bytes


def test_bench_breakdown_lists_unlabeled_paths_under_others(tmp_path: Path) -> None:
    _write_bench(tmp_path)
    _make_app(tmp_path, "frappe")
    _make_site(tmp_path, "site1.local", "db1")
    (tmp_path / "archived").mkdir()
    (tmp_path / "archived" / "old.tar").write_bytes(b"a" * 65536)
    (tmp_path / "sites" / "dist").mkdir()
    (tmp_path / "sites" / "dist" / "image.ext4").write_bytes(b"i" * 32768)
    (tmp_path / "sites" / "alias.local").symlink_to("site1.local")

    breakdown = Bench(tmp_path).storage.measure()
    others = {item.name: item.bytes for item in breakdown.others}

    assert others["archived"] >= 65536
    assert others["sites/dist"] >= 32768
    assert others["sites/alias.local"] < 32768
    assert "apps" not in others and "sites/site1.local" not in others
    assert breakdown.others_bytes == sum(others.values())
    parts = breakdown.apps_bytes + breakdown.env_bytes + breakdown.sites_bytes + breakdown.logs_bytes
    assert breakdown.used_bytes >= parts + breakdown.others_bytes


def test_collect_saves_a_report_that_read_returns(tmp_path: Path) -> None:
    _write_bench(tmp_path)
    _make_app(tmp_path, "frappe")
    _make_site(tmp_path, "site1.local", "db1")
    storage = Bench(tmp_path).storage

    collected = storage.collect()

    assert storage.read() == collected


def test_read_treats_an_unusable_report_as_missing(tmp_path: Path) -> None:
    _write_bench(tmp_path)
    storage = Bench(tmp_path).storage
    storage.path.parent.mkdir(parents=True, exist_ok=True)
    storage.path.write_text('{"used_bytes": 1')

    assert storage.read() is None


def test_a_report_is_stale_after_max_age(tmp_path: Path) -> None:
    _write_bench(tmp_path)
    _make_app(tmp_path, "frappe")
    storage = Bench(tmp_path).storage
    breakdown = storage.collect()

    assert not storage.is_stale(breakdown)
    breakdown.measured_at = (datetime.now(UTC) - storage.max_age - timedelta(seconds=1)).isoformat()
    assert storage.is_stale(breakdown)
    assert json.loads(storage.path.read_text())["measured_at"] != breakdown.measured_at


def test_database_directory_is_measured_when_readable(tmp_path: Path) -> None:
    _write_bench(tmp_path)
    data_dir = tmp_path / "mysql"
    data_dir.mkdir()
    (data_dir / "ibdata1").write_bytes(b"x" * 65536)
    bench = Bench(tmp_path)
    bench._db = Mock(get_data_directory=Mock(return_value=str(data_dir)))

    assert measure_database_directory(bench.storage) >= 65536


@pytest.mark.parametrize(
    "get_data_directory",
    [Mock(return_value=None), Mock(return_value="/missing"), Mock(side_effect=ConnectionRefusedError())],
)
def test_database_directory_is_none_when_it_cannot_be_measured(tmp_path: Path, get_data_directory) -> None:
    _write_bench(tmp_path)
    bench = Bench(tmp_path)
    bench._db = Mock(get_data_directory=get_data_directory)

    assert measure_database_directory(bench.storage) is None
