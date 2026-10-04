from types import SimpleNamespace

import pytest

from pilot.config import SiteConfig
from pilot.core.bench.audit_log import AuditLog
from pilot.core.site import Site
from pilot.tasks import delete_backup
from pilot.tasks.delete_backup import DeleteBackupTask


def _task(tmp_path, filenames, s3_configured=False):
    bench = SimpleNamespace(
        logs_path=tmp_path / "logs",
        sites_path=tmp_path / "sites",
        config=SimpleNamespace(s3=SimpleNamespace(is_configured=s3_configured)),
    )
    bench.site = lambda name: Site(SiteConfig(name=name, apps=[]), bench)
    bench.audit_action = lambda category, fields: AuditLog(bench).append(category, fields)
    return DeleteBackupTask(bench=bench, bench_root=tmp_path, site="site1", filenames=filenames), bench


def test_delete_removes_local_files_and_logs(tmp_path) -> None:
    backups = tmp_path / "sites" / "site1" / "private" / "backups"
    backups.mkdir(parents=True)
    name = "20260101_020000-site1-database.sql.gz"
    (backups / name).write_text("x")

    task, bench = _task(tmp_path, [name])
    task.run()

    assert not (backups / name).exists()
    entries = AuditLog(bench).entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["event"] == "delete"
    assert entry["site"] == "site1"
    assert entry["files"] == [name]
    assert entry["type"] == "backup"


def test_delete_logs_even_when_nothing_matched(tmp_path) -> None:
    (tmp_path / "sites" / "site1" / "private" / "backups").mkdir(parents=True)
    task, bench = _task(tmp_path, ["missing-file.sql.gz"])
    task.run()

    entries = AuditLog(bench).entries()
    assert len(entries) == 1
    assert entries[0]["event"] == "delete"
    assert entries[0]["files"] == []


class FakeOffsite:
    def __init__(self, runs):
        self.runs = runs
        self.deleted = []

    def get_backup(self, site_name, timestamp):
        return self.runs.get(timestamp)

    def delete(self, site_name, timestamp, filename):
        self.deleted.append(filename)


@pytest.fixture
def offsite(monkeypatch):
    fake = FakeOffsite({})
    monkeypatch.setattr(delete_backup.OffsiteBackup, "from_config", lambda *args: fake)
    return fake


def test_delete_removes_offsite_copy_of_a_local_backup(tmp_path, offsite) -> None:
    backups = tmp_path / "sites" / "site1" / "private" / "backups"
    backups.mkdir(parents=True)
    name = "20260101_020000-site1-database.sql.gz"
    (backups / name).write_text("x")
    offsite.runs["20260101_020000"] = {"database": name}

    task, bench = _task(tmp_path, [name], s3_configured=True)
    task.run()

    assert not (backups / name).exists()
    assert offsite.deleted == [name]
    entry = AuditLog(bench).entries()[0]
    assert entry["files"] == [name]
    assert entry["offsite"] is True


def test_delete_skips_s3_for_a_local_only_backup(tmp_path, offsite) -> None:
    backups = tmp_path / "sites" / "site1" / "private" / "backups"
    backups.mkdir(parents=True)
    name = "20260101_020000-site1-database.sql.gz"
    (backups / name).write_text("x")

    task, bench = _task(tmp_path, [name], s3_configured=True)
    task.run()

    assert offsite.deleted == []
    entry = AuditLog(bench).entries()[0]
    assert entry["files"] == [name]
    assert entry["offsite"] is False
