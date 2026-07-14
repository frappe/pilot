import json
from types import SimpleNamespace

from pilot.config.backup_config import BackupConfig
from pilot.core.backup_pruner import BackupPruner

_RUNS = ["20260101_020000", "20260102_020000", "20260103_020000"]  # oldest → newest


def _bench(tmp_path):
    return SimpleNamespace(sites_path=tmp_path / "sites")


def _setup_site(bench, site, retention=BackupConfig(scheme="fifo", keep_last=1)):
    site_dir = bench.sites_path / site
    backups = site_dir / "private" / "backups"
    backups.mkdir(parents=True)
    for ts in _RUNS:
        (backups / f"{ts}-{site}-database.sql.gz").write_text("x")
    if retention is not None:
        config = {"backup_retention": {"scheme": retention.scheme, **retention.counts}}
        (site_dir / "site_config.json").write_text(json.dumps(config))
    return backups


class _FakeOffsite:
    """Records deletions; raises for any run whose timestamp is in ``fail_on``."""

    def __init__(self, runs, fail_on=frozenset()):
        self._runs = runs
        self._fail_on = fail_on
        self.deleted = []

    def list_backups(self, site):
        return {ts: {"database": f"{ts}-db"} for ts in self._runs}

    def delete(self, site, timestamp, filename):
        if timestamp in self._fail_on:
            raise RuntimeError("S3 down")
        self.deleted.append((timestamp, filename))


def test_no_retention_keeps_everything(tmp_path) -> None:
    """Automated backups off (no backup_retention in site_config) → never prune."""
    bench = _bench(tmp_path)
    backups = _setup_site(bench, "site1", retention=None)

    pruner = BackupPruner(bench, "site1")
    pruner._offsite = lambda: None
    assert pruner.prune() == []
    assert all((backups / f"{ts}-site1-database.sql.gz").exists() for ts in _RUNS)


def test_offsite_failure_keeps_local_and_is_not_reported(tmp_path) -> None:
    bench = _bench(tmp_path)
    backups = _setup_site(bench, "site1")
    fake = _FakeOffsite(_RUNS, fail_on={"20260102_020000"})

    pruner = BackupPruner(bench, "site1")
    pruner._offsite = lambda: fake
    pruned = pruner.prune()

    assert pruned == ["20260101_020000"]  # the failing run is not reported as pruned
    assert not (backups / "20260101_020000-site1-database.sql.gz").exists()
    assert (backups / "20260102_020000-site1-database.sql.gz").exists()  # kept intact on S3 error
    assert (backups / "20260103_020000-site1-database.sql.gz").exists()  # newest, never pruned


def test_prunes_with_gfs_scheme(tmp_path) -> None:
    """The pruner honours a GFS policy read from site_config, not just FIFO."""
    bench = _bench(tmp_path)
    runs = [f"202601{day:02d}_020000" for day in range(1, 11)]  # 10 daily runs, Jan 1–10
    backups = bench.sites_path / "site1" / "private" / "backups"
    backups.mkdir(parents=True)
    for ts in runs:
        (backups / f"{ts}-site1-database.sql.gz").write_text("x")
    site_config = {"backup_retention": {"scheme": "gfs", "keep_daily": 3, "keep_weekly": 0, "keep_monthly": 0, "keep_yearly": 0}}
    (bench.sites_path / "site1" / "site_config.json").write_text(json.dumps(site_config))

    pruner = BackupPruner(bench, "site1")
    pruner._offsite = lambda: _FakeOffsite(runs)
    pruned = pruner.prune()

    assert set(pruned) == set(runs[:7])  # keeps the 3 newest days, prunes the rest
    for ts in runs[7:]:
        assert (backups / f"{ts}-site1-database.sql.gz").exists()


def test_prunes_local_and_offsite_when_healthy(tmp_path) -> None:
    bench = _bench(tmp_path)
    backups = _setup_site(bench, "site1")
    fake = _FakeOffsite(_RUNS)

    pruner = BackupPruner(bench, "site1")
    pruner._offsite = lambda: fake
    pruned = pruner.prune()

    assert sorted(pruned) == ["20260101_020000", "20260102_020000"]
    assert {ts for ts, _ in fake.deleted} == {"20260101_020000", "20260102_020000"}
    assert not (backups / "20260101_020000-site1-database.sql.gz").exists()
    assert (backups / "20260103_020000-site1-database.sql.gz").exists()
