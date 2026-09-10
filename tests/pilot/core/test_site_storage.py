"""Tests for the cached per-site storage report."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.core.site.storage import SiteStorageCollector, collect_all_benches


def _bench(tmp_path: Path, db_type: str = "mariadb") -> Bench:
    bench_root = tmp_path / "my-bench"
    bench_root.mkdir(parents=True, exist_ok=True)
    config = BenchConfig.from_flat("my-bench", {"db_type": db_type})
    (bench_root / "bench.toml").write_text(config.dumps())
    return Bench(config, bench_root)


def _make_site(bench: Bench, name: str, db_name: str = "site1_db") -> Path:
    site_path = bench.sites_path / name
    (site_path / "private" / "files").mkdir(parents=True)
    (site_path / "public" / "files").mkdir(parents=True)
    (site_path / "site_config.json").write_text(json.dumps({"db_name": db_name}))
    return site_path


def _collector(bench: Bench, schema_sizes: dict[str, int]) -> SiteStorageCollector:
    bench._db = Mock()
    bench._db.get_schema_sizes.return_value = schema_sizes
    return bench.site_storage


def test_collect_splits_private_public_and_database(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    site_path = _make_site(bench, "site1.local")
    (site_path / "private" / "files" / "a.pdf").write_bytes(b"x" * 40960)
    (site_path / "public" / "files" / "b.jpg").write_bytes(b"x" * 20480)

    report = _collector(bench, {"site1_db": 500}).collect()

    usage = report.sites[0]
    assert usage.name == "site1.local"
    assert usage.private_bytes >= 40960
    assert usage.public_bytes >= 20480
    assert usage.database_bytes == 500
    assert usage.total_bytes == usage.private_bytes + usage.public_bytes + 500


def test_collect_writes_one_file_per_bench(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _make_site(bench, "site1.local")
    _make_site(bench, "site2.local", db_name="site2_db")

    collector = _collector(bench, {"site1_db": 500, "site2_db": 700})
    collector.collect()

    payload = json.loads(collector.path.read_text())
    assert collector.path == bench.logs_path / "site-storage.json"
    assert {site["name"] for site in payload["sites"]} == {"site1.local", "site2.local"}
    assert payload["collected_at"]


def test_get_report_serves_the_file_without_measuring_again(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _make_site(bench, "site1.local")
    collector = _collector(bench, {"site1_db": 500})
    collector.collect()

    with patch.object(SiteStorageCollector, "collect") as collect:
        report = collector.get_report()

    collect.assert_not_called()
    assert report.sites[0].database_bytes == 500


def test_get_report_measures_when_the_file_is_missing(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _make_site(bench, "site1.local")
    collector = _collector(bench, {"site1_db": 500})

    assert not collector.path.exists()
    assert collector.get_report().sites[0].database_bytes == 500
    assert collector.path.exists()


def test_an_old_report_is_served_rather_than_measured_again(tmp_path: Path) -> None:
    """Reading never measures: refreshing belongs to the timer and the
    refresh-storage-usage task, so a page load stays fast however old the
    numbers are."""
    bench = _bench(tmp_path)
    _make_site(bench, "site1.local")
    collector = _collector(bench, {"site1_db": 500})
    collector.collect()

    aged = datetime.now(UTC) - timedelta(days=30)
    payload = json.loads(collector.path.read_text())
    payload["collected_at"] = aged.isoformat()
    collector.path.write_text(json.dumps(payload))

    bench._db.get_schema_sizes.return_value = {"site1_db": 900}
    report = collector.get_report()

    assert report.sites[0].database_bytes == 500
    assert report.collected_at == aged.isoformat()


def test_collect_replaces_the_stored_report(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _make_site(bench, "site1.local")
    collector = _collector(bench, {"site1_db": 500})
    collector.collect()

    bench._db.get_schema_sizes.return_value = {"site1_db": 900}
    collector.collect()

    assert collector.get_report().sites[0].database_bytes == 900


def test_a_corrupt_file_is_measured_again_rather_than_raising(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _make_site(bench, "site1.local")
    collector = _collector(bench, {"site1_db": 500})
    collector.path.parent.mkdir(parents=True, exist_ok=True)
    collector.path.write_text("{ truncated")

    assert collector.read() is None
    assert collector.get_report().sites[0].database_bytes == 500


def test_sqlite_sites_are_measured_from_their_own_database_file(tmp_path: Path) -> None:
    """SQLite has no shared server to query for schema sizes."""
    bench = _bench(tmp_path, db_type="sqlite")
    site_path = _make_site(bench, "site1.local")
    (site_path / "db").mkdir()
    (site_path / "db" / "site1_db.db").write_bytes(b"x" * 40960)

    report = bench.site_storage.collect()

    assert report.sites[0].database_bytes >= 40960


def test_a_bench_that_cannot_be_measured_does_not_stop_the_others(tmp_path: Path, capsys) -> None:
    from pilot.core.site import storage

    healthy = _bench(tmp_path, db_type="sqlite")
    _make_site(healthy, "site1.local")
    broken = _bench(tmp_path / "broken", db_type="sqlite")

    measure = SiteStorageCollector.collect

    def collect(self) -> None:
        if self.bench.path == broken.path:
            raise RuntimeError("database is down")
        return measure(self)

    benches = [(broken.path, broken.config), (healthy.path, healthy.config)]
    with (
        patch.object(storage, "iter_sibling_benches", return_value=benches),
        patch.object(SiteStorageCollector, "collect", collect),
    ):
        collect_all_benches()

    assert "could not collect site storage: database is down" in capsys.readouterr().err
    assert healthy.site_storage.path.exists()
