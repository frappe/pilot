from pathlib import Path
from types import SimpleNamespace

import pytest

from pilot.config import SiteConfig
from pilot.core.bench.audit_log import AuditLog
from pilot.core.bench.backups import BenchBackups
from pilot.core.site import Site
from pilot.core.site.backups import SiteBackups
from pilot.exceptions import CommandError
from pilot.tasks.backup_site import BackupSiteTask


@pytest.fixture(autouse=True)
def _no_disk_space_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests are not about disk space; see the estimate tests in test_site_backup_take."""
    monkeypatch.setattr(SiteBackups, "get_estimated_size", lambda self, with_files=True: 0)


def _task(tmp_path):
    bench = SimpleNamespace(
        path=tmp_path,
        sites_path=tmp_path / "sites",
        logs_path=tmp_path / "logs",
        frappe_call=["python", "-m", "frappe"],
        config=SimpleNamespace(s3=SimpleNamespace(is_configured=False)),
    )
    bench.site = lambda name: Site(SiteConfig(name=name, apps=[]), bench)
    bench.audit_action = lambda category, fields: AuditLog(bench).append(category, fields)
    bench.backups = BenchBackups(bench)
    (bench.sites_path / "site1" / "backups").mkdir(parents=True)
    return BackupSiteTask(bench=bench, bench_root=tmp_path, site="site1", with_files=False), bench


def test_success_exit_but_no_files_records_failure(tmp_path, monkeypatch) -> None:
    """Subprocess exits 0 but leaves no files: record a failed run and exit non-zero
    instead of crashing on max({})."""
    task, bench = _task(tmp_path)
    monkeypatch.setattr("pilot.internal.command.run_command", lambda *a, **k: None)

    with pytest.raises(SystemExit) as exit_info:
        task.run()

    assert exit_info.value.code == 1
    entries = AuditLog(bench).entries()
    assert len(entries) == 1
    assert entries[0]["status"] == "failed"
    assert entries[0]["event"] == "backup"


def test_nonzero_exit_records_failure(tmp_path, monkeypatch) -> None:
    task, bench = _task(tmp_path)

    def fail(*a, **k):
        raise CommandError("frappe backup failed", returncode=2)

    monkeypatch.setattr("pilot.internal.command.run_command", fail)

    with pytest.raises(SystemExit) as exit_info:
        task.run()

    assert exit_info.value.code == 2
    entries = AuditLog(bench).entries()
    assert len(entries) == 1
    assert entries[0]["status"] == "failed"


def test_a_disk_write_failure_is_recorded_for_the_scheduler_retry_limit(tmp_path, monkeypatch) -> None:
    task, bench = _task(tmp_path)

    def fail(*args, **kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr("pilot.internal.command.run_command", fail)
    with pytest.raises(SystemExit) as failure:
        task.run()

    assert failure.value.code == 1
    assert AuditLog(bench).entries()[0]["status"] == "failed"


def test_backup_is_written_outside_private_backups(tmp_path, monkeypatch) -> None:
    """Frappe prunes private/backups on its own schedule."""
    task, bench = _task(tmp_path)
    calls = []

    def fail(argv, **k):
        calls.append(argv)
        raise CommandError("frappe backup failed", returncode=2)

    monkeypatch.setattr("pilot.internal.command.run_command", fail)

    with pytest.raises(SystemExit):
        task.run()

    backup_path = Path(calls[0][calls[0].index("--backup-path") + 1])
    assert backup_path.parent == bench.sites_path / "site1" / "backups"
    assert not backup_path.exists()


@pytest.mark.parametrize(
    ("s3_configured", "encrypt_backup_rows", "streams"),
    [(False, [], False), (True, [], True), (True, [["1"]], False), (True, None, False)],
    ids=["no-s3", "s3", "encrypted", "encryption-unreadable"],
)
def test_backups_stream_only_with_s3_and_encryption_known_off(
    tmp_path, monkeypatch, s3_configured, encrypt_backup_rows, streams
) -> None:
    from pilot.core.site.backups import SiteBackups

    task, bench = _task(tmp_path)
    bench.config.s3.is_configured = s3_configured
    monkeypatch.setattr(
        "pilot.core.site.config.database_state.SiteDatabaseState.query", lambda *args: encrypt_backup_rows
    )
    monkeypatch.setattr(SiteBackups, "stream", lambda self, with_files: ("20261022_114241", {"streamed": 1}))
    disk_file = tmp_path / "20261022_114241-site1-database.sql.gz"
    disk_file.write_bytes(b"x")
    monkeypatch.setattr(SiteBackups, "take", lambda self, with_files: ("20261022_114241", [disk_file]))
    monkeypatch.setattr(BackupSiteTask, "upload", lambda self, timestamp, files: False)
    monkeypatch.setattr(BackupSiteTask, "prune", lambda self: [])

    task.run()

    assert ("streamed" in AuditLog(bench).entries()[0]["files"]) is streams


INDEX = {"files_index": [{"name": "a", "file_url": "/files/a", "size": 1}], "private_files_index": []}


def _indexed_task(tmp_path, monkeypatch, with_files: bool, offsite: bool):
    """A task whose backup succeeds, recording calls to set_files_index."""
    task, bench = _task(tmp_path)
    task.with_files = with_files
    recorded = []
    store = SimpleNamespace(set_files_index=lambda *args: recorded.append(args))
    monkeypatch.setattr(BackupSiteTask, "take", lambda self: ("20261117_162033", {"f": 1}, offsite))
    monkeypatch.setattr(BackupSiteTask, "prune", lambda self: [])
    monkeypatch.setattr(SiteBackups, "get_files_index", lambda self: INDEX)
    monkeypatch.setattr("pilot.tasks.backup_site.OffsiteBackup.from_config", lambda *args: store)
    return task, bench, recorded


def test_offsite_backup_with_files_records_the_index(tmp_path, monkeypatch) -> None:
    """A with-files run that reached S3 gets its File index next to the run's metadata."""
    task, _, recorded = _indexed_task(tmp_path, monkeypatch, with_files=True, offsite=True)

    task.run()

    assert recorded == [("site1", "20261117_162033", INDEX)]


@pytest.mark.parametrize(
    ("with_files", "offsite"), [(False, True), (True, False)], ids=["no-files", "local-only"]
)
def test_no_index_without_files_or_offsite(tmp_path, monkeypatch, with_files, offsite) -> None:
    """The index pairs with the file archives, and needs an S3 run to sit beside."""
    task, _, recorded = _indexed_task(tmp_path, monkeypatch, with_files=with_files, offsite=offsite)

    task.run()

    assert recorded == []


def test_index_failure_keeps_the_backup_a_success(tmp_path, monkeypatch, capsys) -> None:
    """The backup itself is good; a missing index must not mark it failed."""
    task, bench, recorded = _indexed_task(tmp_path, monkeypatch, with_files=True, offsite=True)

    def fail(self):
        raise CommandError("no method", returncode=1)

    monkeypatch.setattr(SiteBackups, "get_files_index", fail)

    task.run()

    assert recorded == []
    assert AuditLog(bench).entries()[0]["status"] == "success"
    assert "Backup file index skipped" in capsys.readouterr().out
