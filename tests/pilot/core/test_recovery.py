import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pilot.config import SiteConfig
from pilot.core.site import Site
from pilot.core.site.recovery import SiteRecovery
from pilot.exceptions import BenchError


def _s3_config(configured: bool = True) -> SimpleNamespace:
    if configured:
        return SimpleNamespace(
            is_configured=True,
            bucket="test-bucket",
            endpoint_url="https://s3.example.com",
            access_key="test-key",
            secret_key="test-secret",
            region="us-east-1",
        )
    return SimpleNamespace(is_configured=False, bucket="")


def _setup_bench_and_site(tmp_path: Path, s3_configured: bool = True):
    bench_dir = tmp_path / "test-bench"
    sites_dir = bench_dir / "sites"
    site_dir = sites_dir / "site1.localhost"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps({"maintenance_mode": 0, "pause_scheduler": 0}))

    bench = SimpleNamespace(
        path=bench_dir,
        sites_path=sites_dir,
        config=SimpleNamespace(name="test-bench", s3=_s3_config(s3_configured)),
    )
    site = Site(SiteConfig(name="site1.localhost", apps=[]), bench)
    return bench, site


def test_recovery_unconfigured_s3_raises(tmp_path: Path) -> None:
    _bench, site = _setup_bench_and_site(tmp_path, s3_configured=False)

    recovery = SiteRecovery(site)
    with pytest.raises(BenchError, match="S3 offsite backups are not configured"):
        recovery.recover()


def test_recovery_no_backups_found_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _bench, site = _setup_bench_and_site(tmp_path)

    fake_offsite = MagicMock()
    fake_offsite.list_backups.return_value = {}
    monkeypatch.setattr("pilot.core.site.recovery.OffsiteBackup.from_config", lambda *args, **kwargs: fake_offsite)

    recovery = SiteRecovery(site)
    with pytest.raises(BenchError, match="No offsite backups found"):
        recovery.recover()


def test_recovery_specific_timestamp_not_found_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _bench, site = _setup_bench_and_site(tmp_path)

    fake_offsite = MagicMock()
    fake_offsite.get_backup.return_value = None
    monkeypatch.setattr("pilot.core.site.recovery.OffsiteBackup.from_config", lambda *args, **kwargs: fake_offsite)

    recovery = SiteRecovery(site)
    with pytest.raises(BenchError, match=r"No offsite backup found for site 'site1\.localhost' at timestamp '20260101_000000'"):
        recovery.recover(timestamp="20260101_000000")


def test_successful_recovery_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _bench, site = _setup_bench_and_site(tmp_path)
    ts = "20260927_140002"

    fake_offsite = MagicMock()
    fake_offsite.list_backups.return_value = {
        ts: {
            "database": f"{ts}-site1.localhost-database.sql.gz",
            "files": f"{ts}-site1.localhost-files.tar",
            "private_files": f"{ts}-site1.localhost-private-files.tar",
            "site_config": f"{ts}-site1.localhost-site_config_backup.json",
        }
    }

    def fake_download(site_name, timestamp, filename, dest):
        dest.write_text("dummy-backup-data")

    fake_offsite.download.side_effect = fake_download
    monkeypatch.setattr("pilot.core.site.recovery.OffsiteBackup.from_config", lambda *args, **kwargs: fake_offsite)

    # Mock site restore, migrate, and clear_cache directly on Site.
    site.restore = MagicMock()
    site.migrate = MagicMock(return_value="")
    site.clear_cache = MagicMock()

    progress_messages = []
    restored_ts = site.recover(on_progress=lambda msg: progress_messages.append(msg))

    assert restored_ts == ts
    site.restore.assert_called_once()
    site.migrate.assert_called_once_with(skip_failing=False)
    site.clear_cache.assert_called_once()

    # Maintenance mode should be restored to False (prior state was 0).
    assert site.maintenance_mode is False

    # Downloads go to a temp dir, so the site's own backup dir should be untouched.
    backups_dir = site.path / "private" / "backups"
    assert not (backups_dir / f"{ts}-site1.localhost-database.sql.gz").exists()
    assert not (backups_dir / f"{ts}-site1.localhost-site_config_backup.json").exists()


def test_recovery_leave_maintenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _bench, site = _setup_bench_and_site(tmp_path)
    ts = "20260927_140002"

    fake_offsite = MagicMock()
    fake_offsite.list_backups.return_value = {
        ts: {
            "database": f"{ts}-site1.localhost-database.sql.gz",
        }
    }
    fake_offsite.download.side_effect = lambda site_name, timestamp, filename, dest: dest.write_text("dummy")
    monkeypatch.setattr("pilot.core.site.recovery.OffsiteBackup.from_config", lambda *args, **kwargs: fake_offsite)

    site.restore = MagicMock()
    site.migrate = MagicMock(return_value="")
    site.clear_cache = MagicMock()

    site.recover(leave_maintenance=True)
    assert site.maintenance_mode is True


def test_recovery_preserves_prior_isolation_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Recovery must restore the pre-existing maintenance/scheduler settings, not force them off."""
    _bench, site = _setup_bench_and_site(tmp_path)

    # Site was already in maintenance mode before recovery started.
    (site.path / "site_config.json").write_text(
        json.dumps({"maintenance_mode": 1, "pause_scheduler": 1})
    )
    ts = "20260927_140002"

    fake_offsite = MagicMock()
    fake_offsite.list_backups.return_value = {
        ts: {"database": f"{ts}-site1.localhost-database.sql.gz"}
    }
    fake_offsite.download.side_effect = lambda site_name, timestamp, filename, dest: dest.write_text("dummy")
    monkeypatch.setattr("pilot.core.site.recovery.OffsiteBackup.from_config", lambda *args, **kwargs: fake_offsite)

    site.restore = MagicMock()
    site.migrate = MagicMock(return_value="")
    site.clear_cache = MagicMock()

    site.recover(leave_maintenance=False)
    # Prior state (maintenance=1, pause_scheduler=1) must be preserved, not cleared.
    assert site.maintenance_settings == {"maintenance_mode": 1, "pause_scheduler": 1}


def test_recovery_rejects_path_traversal_filename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An absolute or dotdot artifact filename must raise BenchError before any download."""
    _bench, site = _setup_bench_and_site(tmp_path)
    ts = "20260927_140002"

    fake_offsite = MagicMock()
    # get_backup is called when a specific timestamp is requested.
    fake_offsite.get_backup.return_value = {"database": "../../../etc/passwd"}
    monkeypatch.setattr("pilot.core.site.recovery.OffsiteBackup.from_config", lambda *args, **kwargs: fake_offsite)

    recovery = SiteRecovery(site)
    with pytest.raises(BenchError, match="unsafe database filename"):
        recovery.recover(timestamp=ts)

    # No download must have been attempted.
    fake_offsite.download.assert_not_called()


def test_recovery_setup_failure_does_not_strand_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If download or setup fails before data is modified, prior isolation state must be restored."""
    _bench, site = _setup_bench_and_site(tmp_path)
    ts = "20260927_140002"

    fake_offsite = MagicMock()
    fake_offsite.list_backups.return_value = {ts: {"database": f"{ts}-site1.localhost-database.sql.gz"}}
    fake_offsite.download.side_effect = RuntimeError("S3 connection drop")
    monkeypatch.setattr("pilot.core.site.recovery.OffsiteBackup.from_config", lambda *args, **kwargs: fake_offsite)

    with pytest.raises(RuntimeError, match="S3 connection drop"):
        site.recover()

    # Prior state (maintenance=0, pause_scheduler=0) must be restored, not left stranded in maintenance.
    assert site.maintenance_mode is False
    assert site.maintenance_settings == {"maintenance_mode": 0, "pause_scheduler": 0}


def test_recovery_post_restore_failure_keeps_site_isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If migration or restore fails after data is modified, site must remain isolated in maintenance mode."""
    _bench, site = _setup_bench_and_site(tmp_path)
    ts = "20260927_140002"

    fake_offsite = MagicMock()
    fake_offsite.list_backups.return_value = {ts: {"database": f"{ts}-site1.localhost-database.sql.gz"}}
    fake_offsite.download.side_effect = lambda site_name, timestamp, filename, dest: dest.write_text("dummy")
    monkeypatch.setattr("pilot.core.site.recovery.OffsiteBackup.from_config", lambda *args, **kwargs: fake_offsite)

    site.restore = MagicMock()
    # Migration fails after database has already been restored!
    site.migrate = MagicMock(side_effect=BenchError("Migration failed"))
    site.clear_cache = MagicMock()

    with pytest.raises(BenchError, match="Migration failed"):
        site.recover()

    # Site data was altered, so the site MUST remain isolated to prevent traffic/workers hitting bad state.
    assert site.maintenance_mode is True
    assert site.maintenance_settings == {"maintenance_mode": 1, "pause_scheduler": 1}


def test_recovery_stages_downloads_in_bench_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Downloads must stage inside the site's private/backups directory on bench storage, not system /tmp."""
    _bench, site = _setup_bench_and_site(tmp_path)
    ts = "20260927_140002"

    recorded_destinations: list[Path] = []
    fake_offsite = MagicMock()
    fake_offsite.list_backups.return_value = {ts: {"database": f"{ts}-site1.localhost-database.sql.gz"}}

    def fake_download(site_name, timestamp, filename, dest):
        recorded_destinations.append(dest)
        dest.write_text("dummy")

    fake_offsite.download.side_effect = fake_download
    monkeypatch.setattr("pilot.core.site.recovery.OffsiteBackup.from_config", lambda *args, **kwargs: fake_offsite)

    site.restore = MagicMock()
    site.migrate = MagicMock(return_value="")
    site.clear_cache = MagicMock()

    site.recover()

    assert recorded_destinations
    expected_parent = site.path / "private" / "backups"
    for dest in recorded_destinations:
        # Must be located in site's private/backups directory on bench disk
        assert expected_parent in dest.parents
        # Must be inside a hidden temporary recovery folder
        assert dest.parent.name.startswith(".recovery-")
        # Temporary folder should be cleaned up after recovery
        assert not dest.exists()
