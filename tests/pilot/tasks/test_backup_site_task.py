from types import SimpleNamespace

import pytest

from pilot.tasks import backup_site as mod
from pilot.tasks.backup_site import BackupSiteTask
from pilot.core.audit_log import AuditLog


def _task(tmp_path):
    bench = SimpleNamespace(
        sites_path=tmp_path / "sites",
        logs_path=tmp_path / "logs",
        frappe_call=["python", "-m", "frappe"],
        config=SimpleNamespace(s3=SimpleNamespace(is_configured=False)),
    )
    (bench.sites_path / "site1" / "private" / "backups").mkdir(parents=True)
    return BackupSiteTask(bench=bench, bench_root=tmp_path, site="site1", with_files=False), bench


def test_success_exit_but_no_files_records_failure(tmp_path, monkeypatch) -> None:
    """Subprocess exits 0 but leaves no files: record a failed run and exit non-zero
    instead of crashing on max({})."""
    task, bench = _task(tmp_path)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))

    with pytest.raises(SystemExit) as exit_info:
        task.run()

    assert exit_info.value.code == 1
    entries = AuditLog(bench).entries()
    assert len(entries) == 1
    assert entries[0]["status"] == "failed"
    assert entries[0]["event"] == "backup"


def test_nonzero_exit_records_failure(tmp_path, monkeypatch) -> None:
    task, bench = _task(tmp_path)
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=2))

    with pytest.raises(SystemExit) as exit_info:
        task.run()

    assert exit_info.value.code == 2
    entries = AuditLog(bench).entries()
    assert len(entries) == 1
    assert entries[0]["status"] == "failed"
