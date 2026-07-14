from datetime import date, timedelta

import pytest

from pilot.config.backup_config import BackupConfig
from pilot.config.bench_config import BenchConfig
from pilot.core.backup_retention import BackupRetentionPolicy
from pilot.exceptions import ConfigError
from pilot.config.toml_writer import bench_config_to_toml

# ── Retention policy ────────────────────────────────────────────────────────────


def _daily_runs(days: int, start=date(2026, 1, 1)) -> list[str]:
    return [(start + timedelta(days=i)).strftime("%Y%m%d") + "_020000" for i in range(days)]


def test_fifo_keeps_newest_n() -> None:
    runs = _daily_runs(10)
    policy = BackupRetentionPolicy(BackupConfig(scheme="fifo", keep_last=3))
    deletions = policy.select_deletions(runs)
    assert len(deletions) == 7
    assert sorted(set(runs) - set(deletions)) == runs[-3:]


def test_fifo_always_keeps_latest_even_at_zero() -> None:
    runs = _daily_runs(5)
    policy = BackupRetentionPolicy(BackupConfig(scheme="fifo", keep_last=0))
    kept = set(runs) - set(policy.select_deletions(runs))
    assert kept == {runs[-1]}


def test_single_run_is_never_deleted() -> None:
    policy = BackupRetentionPolicy(BackupConfig(scheme="gfs"))
    assert policy.select_deletions(["20260101_020000"]) == []


def test_gfs_keeps_daily_weekly_monthly_yearly() -> None:
    runs = _daily_runs(90)
    policy = BackupRetentionPolicy(
        BackupConfig(scheme="gfs", keep_daily=7, keep_weekly=4, keep_monthly=6, keep_yearly=1)
    )
    kept = sorted(set(runs) - set(policy.select_deletions(runs)))
    # The 7 most recent days are always among the kept runs.
    assert set(runs[-7:]).issubset(kept)
    # Newest run survives; older runs get pruned.
    assert runs[-1] in kept
    assert len(kept) < len(runs)


def test_gfs_multiple_runs_same_day_keeps_latest_of_day() -> None:
    runs = ["20260110_010000", "20260110_230000", "20260111_020000"]
    policy = BackupRetentionPolicy(BackupConfig(scheme="gfs", keep_daily=2, keep_weekly=0, keep_monthly=0, keep_yearly=0))
    deletions = policy.select_deletions(runs)
    assert deletions == ["20260110_010000"]  # earlier run of the 10th is dropped


def test_unparseable_timestamps_are_ignored() -> None:
    runs = ["not-a-timestamp", "20260101_020000", "20260102_020000"]
    policy = BackupRetentionPolicy(BackupConfig(scheme="fifo", keep_last=1))
    deletions = policy.select_deletions(runs)
    assert "not-a-timestamp" not in deletions


# ── Pruner (local files) ────────────────────────────────────────────────────────


class _FakeS3Config:
    is_configured = False


class _FakeConfig:
    def __init__(self, backup: BackupConfig) -> None:
        self.backup = backup
        self.s3 = _FakeS3Config()


class _FakeBench:
    def __init__(self, sites_path, backup: BackupConfig) -> None:
        self.sites_path = sites_path
        self.config = _FakeConfig(backup)


def test_pruner_deletes_old_local_backups(tmp_path) -> None:
    from pilot.core.backup_pruner import BackupPruner

    backups = tmp_path / "site1" / "private" / "backups"
    backups.mkdir(parents=True)
    timestamps = _daily_runs(5)
    for ts in timestamps:
        for part in ("database.sql.gz", "files.tar"):
            (backups / f"{ts}-site1-{part}").write_text("x")

    bench = _FakeBench(tmp_path, BackupConfig(scheme="fifo", keep_last=2))
    pruned = BackupPruner(bench, "site1").prune()

    assert sorted(pruned) == timestamps[:3]
    surviving = {f.name.split("-", 1)[0] for f in backups.iterdir()}
    assert surviving == set(timestamps[-2:])


# ── Config round-trip & validation ──────────────────────────────────────────────


def test_backup_config_roundtrips_through_toml() -> None:
    data = {
        "bench": {"name": "test-bench", "python": "3.14"},
        "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
        "mariadb": {"root_password": "root"},
        "redis": {"cache_port": 13000, "queue_port": 11000},
        "admin": {"domain": "admin.test.localhost"},
        "backup": {"scheme": "fifo", "keep_last": 3, "keep_daily": 2},
    }
    config = BenchConfig._from_dict(data)
    config.validate()
    assert config.backup.scheme == "fifo"
    assert config.backup.keep_last == 3

    import tomllib

    reparsed = BenchConfig._from_dict(tomllib.loads(bench_config_to_toml(config)))
    reparsed.validate()
    assert reparsed.backup.scheme == "fifo"
    assert reparsed.backup.keep_last == 3
    assert reparsed.backup.keep_daily == 2


def test_backup_defaults_are_gfs() -> None:
    config = BackupConfig()
    assert config.scheme == "gfs"


def test_invalid_scheme_rejected() -> None:
    config = BenchConfig._from_dict(
        {
            "bench": {"name": "test-bench", "python": "3.14"},
            "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
            "mariadb": {"root_password": "root"},
            "redis": {"cache_port": 13000, "queue_port": 11000},
            "admin": {"domain": "admin.test.localhost"},
            "backup": {"scheme": "weekly"},
        }
    )
    with pytest.raises(ConfigError):
        config.validate()


def test_negative_count_rejected() -> None:
    config = BenchConfig._from_dict(
        {
            "bench": {"name": "test-bench", "python": "3.14"},
            "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
            "mariadb": {"root_password": "root"},
            "redis": {"cache_port": 13000, "queue_port": 11000},
            "admin": {"domain": "admin.test.localhost"},
            "backup": {"keep_daily": -1},
        }
    )
    with pytest.raises(ConfigError):
        config.validate()
