from types import SimpleNamespace

import pytest

from pilot.config import SiteConfig
from pilot.core.bench.audit_log import AuditLog
from pilot.core.site import Site
from pilot.exceptions import CommandError
from pilot.tasks.backup_site import BackupSiteTask


def _task(tmp_path):
    bench = SimpleNamespace(
        sites_path=tmp_path / "sites",
        logs_path=tmp_path / "logs",
        frappe_call=["python", "-m", "frappe"],
        config=SimpleNamespace(s3=SimpleNamespace(is_configured=False)),
    )
    bench.site = lambda name: Site(SiteConfig(name=name, apps=[]), bench)
    bench.audit_action = lambda category, fields: AuditLog(bench).append(category, fields)
    (bench.sites_path / "site1" / "backups").mkdir(parents=True)
    return BackupSiteTask(bench=bench, bench_root=tmp_path, site="site1", with_files=False), bench


def test_success_exit_but_no_files_records_failure(tmp_path, monkeypatch) -> None:
    """Subprocess exits 0 but leaves no files: record a failed run and exit non-zero
    instead of crashing on max({})."""
    task, bench = _task(tmp_path)
    monkeypatch.setattr("pilot.utils.run_command", lambda *a, **k: None)

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

    monkeypatch.setattr("pilot.utils.run_command", fail)

    with pytest.raises(SystemExit) as exit_info:
        task.run()

    assert exit_info.value.code == 2
    entries = AuditLog(bench).entries()
    assert len(entries) == 1
    assert entries[0]["status"] == "failed"


def test_backup_is_written_outside_private_backups(tmp_path, monkeypatch) -> None:
    """Frappe prunes private/backups on its own schedule."""
    task, bench = _task(tmp_path)
    calls = []

    def fail(argv, **k):
        calls.append(argv)
        raise CommandError("frappe backup failed", returncode=2)

    monkeypatch.setattr("pilot.utils.run_command", fail)

    with pytest.raises(SystemExit):
        task.run()

    assert calls[0][-2:] == ["--backup-path", str(bench.sites_path / "site1" / "backups")]
