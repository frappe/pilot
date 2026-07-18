from types import SimpleNamespace

from pilot.tasks.delete_backup import DeleteBackupTask
from pilot.core.audit_log import AuditLog


def _task(tmp_path, filenames):
    bench = SimpleNamespace(
        logs_path=tmp_path / "logs",
        sites_path=tmp_path / "sites",
        config=SimpleNamespace(s3=SimpleNamespace(is_configured=False)),
    )
    return DeleteBackupTask(
        bench=bench, bench_root=tmp_path, site="site1", filenames=filenames
    ), bench


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
