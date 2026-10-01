from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pilot.config import SiteConfig
from pilot.core.site import Site
from pilot.exceptions import BenchError
from pilot.tasks.restore_site import RestoreSiteTask


def _bench(tmp_path):
    bench = SimpleNamespace(sites_path=tmp_path / "sites")
    bench.site = lambda name: Site(SiteConfig(name=name, apps=[]), bench)
    return bench


def _backup_file(tmp_path, name: str) -> str:
    backups = tmp_path / "sites" / "source.localhost" / "private" / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    path = backups / name
    path.write_text("x")
    return str(path)


def test_get_restore_files_maps_each_backup_kind(tmp_path) -> None:
    database = _backup_file(tmp_path, "20260101_020000-source_localhost-database.sql.gz")
    public = _backup_file(tmp_path, "20260101_020000-source_localhost-files.tar")
    private = _backup_file(tmp_path, "20260101_020000-source_localhost-private-files.tar")
    _backup_file(tmp_path, "20260101_020000-source_localhost-site_config_backup.json")
    _backup_file(tmp_path, "20260102_020000-source_localhost-database.sql.gz")

    files = _bench(tmp_path).site("source.localhost").backups.get_restore_files("20260101_020000")

    assert files == {"db_file": database, "public_files": public, "private_files": private}


def test_get_restore_files_needs_a_local_database(tmp_path) -> None:
    _backup_file(tmp_path, "20260101_020000-source_localhost-files.tar")

    with pytest.raises(BenchError, match="no local database"):
        _bench(tmp_path).site("source.localhost").backups.get_restore_files("20260101_020000")


def test_get_restore_files_rejects_a_glob_timestamp(tmp_path) -> None:
    _backup_file(tmp_path, "20260101_020000-source_localhost-database.sql.gz")

    with pytest.raises(BenchError):
        _bench(tmp_path).site("source.localhost").backups.get_restore_files("*")


def _task(tmp_path) -> RestoreSiteTask:
    target = tmp_path / "sites" / "target.localhost"
    target.mkdir(parents=True)
    (target / "site_config.json").write_text("{}")
    return RestoreSiteTask(
        bench=_bench(tmp_path),
        bench_root=tmp_path,
        site="target.localhost",
        source_site="source.localhost",
        timestamp="20260101_020000",
    )


def test_run_restores_the_source_backup_then_migrates_under_maintenance(tmp_path) -> None:
    database = _backup_file(tmp_path, "20260101_020000-source_localhost-database.sql.gz")
    task = _task(tmp_path)
    calls = []

    def record(name):
        return lambda site, *args, **kwargs: calls.append((name, site.maintenance_mode, args, kwargs))

    with (
        patch.object(Site, "restore", record("restore")),
        patch.object(Site, "migrate", record("migrate")),
    ):
        task.run()

    assert calls == [
        ("restore", True, (), {"db_file": database}),
        ("migrate", True, (), {}),
    ]
    assert not task.site_record.maintenance_mode


def test_a_failed_migration_leaves_the_site_in_maintenance_mode(tmp_path) -> None:
    _backup_file(tmp_path, "20260101_020000-source_localhost-database.sql.gz")
    task = _task(tmp_path)

    def fail(site, *args, **kwargs):
        raise BenchError("migration failed")

    with (
        patch.object(Site, "restore", lambda site, **files: None),
        patch.object(Site, "migrate", fail),
        pytest.raises(BenchError),
    ):
        task.run()

    assert task.site_record.maintenance_mode


def test_a_retry_after_a_failure_brings_the_site_back_online(tmp_path) -> None:
    _backup_file(tmp_path, "20260101_020000-source_localhost-database.sql.gz")
    task = _task(tmp_path)

    def fail(site, *args, **kwargs):
        raise BenchError("migration failed")

    with patch.object(Site, "restore", lambda site, **files: None):
        with patch.object(Site, "migrate", fail), pytest.raises(BenchError):
            task.run()
        with patch.object(Site, "migrate", lambda site: None):
            task.run()

    assert task.site_record.maintenance_settings == {"maintenance_mode": 0, "pause_scheduler": 0}
    assert "pilot_maintenance_before_restore" not in (task.site_record.path / "site_config.json").read_text()


def test_a_site_that_was_in_maintenance_mode_stays_in_it(tmp_path) -> None:
    _backup_file(tmp_path, "20260101_020000-source_localhost-database.sql.gz")
    task = _task(tmp_path)
    task.site_record.set_maintenance_mode(True)

    with (
        patch.object(Site, "restore", lambda site, **files: None),
        patch.object(Site, "migrate", lambda site: None),
    ):
        task.run()

    assert task.site_record.maintenance_mode


def test_a_missing_backup_fails_before_the_site_goes_offline(tmp_path) -> None:
    task = _task(tmp_path)

    with patch.object(Site, "restore") as restore, pytest.raises(BenchError, match="no local"):
        task.run()

    restore.assert_not_called()
    assert not task.site_record.maintenance_mode
    assert "pilot_maintenance_before_restore" not in (task.site_record.path / "site_config.json").read_text()
