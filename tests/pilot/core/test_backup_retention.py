from datetime import date, timedelta

from pilot.config.backup import BackupConfig
from pilot.core.backup_retention import BackupRetentionPolicy

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


def test_gfs_yearly_tier_keeps_one_run_per_year() -> None:
    """Runs spanning a year boundary: the yearly tier keeps the latest of each year."""
    runs = _daily_runs(400)  # ~13 months, Jan 2026 into Feb 2027
    policy = BackupRetentionPolicy(BackupConfig(scheme="gfs", keep_daily=0, keep_weekly=0, keep_monthly=0, keep_yearly=2))
    kept = sorted(set(runs) - set(policy.select_deletions(runs)))
    latest_2026 = max(r for r in runs if r.startswith("2026"))
    latest_2027 = max(r for r in runs if r.startswith("2027"))
    assert kept == sorted({latest_2026, latest_2027})


def test_backup_defaults_are_gfs() -> None:
    config = BackupConfig()
    assert config.scheme == "gfs"
