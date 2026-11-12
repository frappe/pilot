import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.core.bench import Bench
from pilot.core.site.backups import SYSTEM_RESERVED_BYTES, SiteBackups
from pilot.exceptions import BenchError, InsufficientDiskSpaceError
from tests.pilot.commands.test_commands import make_bench


@pytest.fixture
def no_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SiteBackups, "estimated_database_size", 0)


def _site_with_backups(tmp_path: Path) -> tuple[Bench, Path]:
    bench = make_bench(tmp_path)
    backups = bench.sites_path / "site1.localhost" / "backups"
    backups.mkdir(parents=True)
    return bench, backups


def test_a_backup_returns_its_own_run_while_another_one_is_written(tmp_path: Path, no_estimate) -> None:
    bench, backups = _site_with_backups(tmp_path)
    # A scheduled backup started later and is still writing its newer run.
    (backups / "20261015_030000-site1-database.sql.gz").write_text("half written")

    def frappe_backup(argv, **kwargs):
        staging = Path(argv[argv.index("--backup-path") + 1])
        (staging / "20261015_020000-site1-database.sql.gz").write_text("complete")
        (staging / "20261015_020000-site1-files.tar").write_text("complete")

    with patch("pilot.internal.command.run_command", side_effect=frappe_backup):
        timestamp, files = bench.site("site1.localhost").backups.take(with_files=True)

    assert timestamp == "20261015_020000"
    assert sorted(path.name for path in files) == [
        "20261015_020000-site1-database.sql.gz",
        "20261015_020000-site1-files.tar",
    ]
    assert all(path.parent == backups for path in files)
    assert sorted(path.name for path in backups.iterdir()) == [
        "20261015_020000-site1-database.sql.gz",
        "20261015_020000-site1-files.tar",
        "20261015_030000-site1-database.sql.gz",
    ]


def test_a_backup_never_overwrites_a_run_from_the_same_second(tmp_path: Path, no_estimate) -> None:
    bench, backups = _site_with_backups(tmp_path)
    (backups / "20261015_020000-site1-database.sql.gz").write_text("other run")

    def frappe_backup(argv, **kwargs):
        staging = Path(argv[argv.index("--backup-path") + 1])
        (staging / "20261015_020000-site1-database.sql.gz").write_text("this run")

    with (
        patch("pilot.internal.command.run_command", side_effect=frappe_backup),
        pytest.raises(BenchError, match="same second"),
    ):
        bench.site("site1.localhost").backups.take(with_files=False)

    assert (backups / "20261015_020000-site1-database.sql.gz").read_text() == "other run"
    assert [path.name for path in backups.iterdir()] == ["20261015_020000-site1-database.sql.gz"]


def test_retention_deletes_offsite_without_holding_the_publish_lock(tmp_path: Path) -> None:
    """A slow bucket must not hold back a backup that waits to publish its run."""
    from unittest.mock import Mock

    from pilot.internal.atomic_file import exclusive_file_lock

    bench, backups = _site_with_backups(tmp_path)
    site_backups = bench.site("site1.localhost").backups
    (backups / "20261015_010000-site1-database.sql.gz").write_text("old")
    (backups / "20261015_020000-site1-database.sql.gz").write_text("new")
    offsite = Mock()
    offsite.list_backups.return_value = {
        "20261015_010000": {"database": "20261015_010000-site1-database.sql.gz"}
    }

    def delete(*_args) -> None:
        with exclusive_file_lock(site_backups.runs_lock_path, blocking=False):
            pass

    offsite.delete.side_effect = delete
    policy = Mock()
    policy.select_deletions.return_value = ["20261015_010000"]

    with (
        patch("pilot.core.site.backups.read_retention", return_value=object()),
        patch("pilot.core.site.backups.BackupRetentionPolicy", return_value=policy),
        patch.object(type(site_backups), "_offsite", return_value=offsite),
    ):
        assert site_backups.prune() == ["20261015_010000"]

    offsite.delete.assert_called_once()
    assert [path.name for path in backups.iterdir()] == ["20261015_020000-site1-database.sql.gz"]


def _disk(free: int):
    return patch("shutil.disk_usage", return_value=shutil._ntuple_diskusage(10**12, 10**12 - free, free))


def test_the_newest_dump_with_headroom_predicts_the_database_part(tmp_path: Path) -> None:
    bench, backups = _site_with_backups(tmp_path)
    (backups / "20261014_020000-site1-database.sql.gz").write_bytes(b"x" * 500)
    (backups / "20261015_020000-site1-database.sql.gz").write_bytes(b"x" * 1000)

    with patch("pilot.core.site.storage.SiteStorageCollector.get_database_bytes") as database_bytes:
        size = bench.site("site1.localhost").backups.get_estimated_size(with_files=False)

    assert size == 1200
    database_bytes.assert_not_called()


def test_without_a_dump_the_database_and_files_size_is_the_estimate(tmp_path: Path) -> None:
    bench, _ = _site_with_backups(tmp_path)
    site = bench.site("site1.localhost")

    with (
        patch(
            "pilot.core.site.storage.SiteStorageCollector.get_database_bytes",
            return_value={"site1.localhost": 7000},
        ),
        patch(
            "pilot.internal.host_resources.DiskUsage.bytes",
            property(lambda self: 300 if self.path.parent.name == "public" else 200),
        ),
    ):
        assert site.backups.get_estimated_size(with_files=True) == 7500
        assert site.backups.get_estimated_size(with_files=False) == 7000


def test_a_backup_the_disk_cannot_hold_is_refused_before_frappe_runs(tmp_path: Path) -> None:
    bench, backups = _site_with_backups(tmp_path)
    (backups / "20261015_020000-site1-database.sql.gz").write_bytes(b"x" * 1000)

    with (
        _disk(free=1100),
        patch("pilot.internal.command.run_command") as frappe_backup,
        pytest.raises(InsufficientDiskSpaceError, match=r"the backup of site1\.localhost") as error,
    ):
        bench.site("site1.localhost").backups.take(with_files=False)

    frappe_backup.assert_not_called()
    assert (error.value.needed_bytes, error.value.free_bytes) == (1200, 1100)


def test_a_backup_the_disk_can_hold_with_the_system_reserve_goes_ahead(tmp_path: Path) -> None:
    bench, backups = _site_with_backups(tmp_path)
    (backups / "20261015_020000-site1-database.sql.gz").write_bytes(b"x" * 1000)

    with _disk(free=SYSTEM_RESERVED_BYTES + 1200):
        bench.site("site1.localhost").backups.ensure_disk_space(with_files=False)


def test_a_backup_that_would_eat_into_the_system_reserve_is_refused(tmp_path: Path) -> None:
    bench, backups = _site_with_backups(tmp_path)
    (backups / "20261015_020000-site1-database.sql.gz").write_bytes(b"x" * 1000)

    with _disk(free=SYSTEM_RESERVED_BYTES + 1199), pytest.raises(InsufficientDiskSpaceError):
        bench.site("site1.localhost").backups.ensure_disk_space(with_files=False)


def test_the_refusal_names_sizes_a_person_can_read() -> None:
    error = InsufficientDiskSpaceError(
        "the backup of a.localhost", 3 * 1024**3 // 2, 200 * 1024**2, 500 * 1024**2
    )

    assert str(error) == (
        "Not enough disk space for the backup of a.localhost: it needs about 1.5 GB, "
        "plus 500 MB kept free for the system, and 200 MB is free."
    )
    assert InsufficientDiskSpaceError.format_size(400 * 1024) == "400 KB"
