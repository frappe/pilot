from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config import BackupConfig
from pilot.config.site_backup import write_retention
from pilot.core.bench.audit_log import AuditLog
from pilot.core.site import Site
from pilot.internal.tasks.store import TaskStore
from pilot.tasks.backup_site import BackupSiteTask
from tests.pilot.commands.test_commands import make_bench

_NOW = datetime(2026, 10, 18, 8, tzinfo=UTC)


def _site(tmp_path: Path) -> Site:
    bench = make_bench(tmp_path)
    bench.create_directories()
    bench.config.write(bench.path)
    site = bench.site("test.localhost")
    site.path.mkdir(parents=True)
    write_retention(site.path / "site_config.json", BackupConfig(calendar_rotation=True))
    return site


def _record(site: Site, when: datetime, **fields) -> None:
    AuditLog(site.bench).append(
        "backup",
        {
            "site": site.config.name, "event": "backup", "status": "success",
            "finished_at": when.isoformat(), "logged_at": when.isoformat(), **fields,
        },
    )


@pytest.mark.parametrize(
    ("now", "history", "expected"),
    [
        (_NOW, [], {"with_files": True}),
        (_NOW, [(timedelta(hours=1), {})], None),
        (_NOW, [(timedelta(hours=22, minutes=55), {})], None),
        (_NOW, [(timedelta(hours=23), {})], {"with_files": True}),
        (_NOW.replace(hour=23, minute=30), [(timedelta(hours=23, minutes=1), {})], {"with_files": False}),
        (_NOW, [(timedelta(minutes=10 * n), {"status": "failed"}) for n in range(5)], {"with_files": True}),
        (_NOW, [(timedelta(minutes=10 * n), {"status": "failed"}) for n in range(6)], None),
    ],
    ids=["first", "recent", "under-23h", "23h", "files-done-today", "5-failures", "6-failures"],
)
def test_when_a_scheduled_backup_is_due(tmp_path: Path, now: datetime, history: list, expected: dict | None) -> None:
    site = _site(tmp_path)
    for age, fields in reversed(history):
        _record(site, now - age, **{"with_files": True, **fields})

    assert site.backups.get_scheduled_backup(now) == expected


@pytest.mark.parametrize("manual_backup", [False, True])
def test_polls_do_not_overlap_a_queued_site_backup(tmp_path: Path, manual_backup: bool) -> None:
    site = _site(tmp_path)
    with patch("pilot.managers.task.control.TaskWorkerControl.wake"):
        if manual_backup:
            BackupSiteTask.queue(
                site.bench, site=site.config.name, with_files=True, resource_key=f"site:{site.config.name}"
            )
        site.bench.backups.queue_scheduled(_NOW)
        site.bench.backups.queue_scheduled(_NOW + timedelta(hours=1))

    task_dirs = list((site.bench.path / "tasks").glob("*/meta.json"))
    assert len(task_dirs) == 1
    metadata = TaskStore(site.bench.path).read_metadata(task_dirs[0].parent.name)
    assert metadata["command"] == "backup-site"
    assert metadata["resource_keys"] == [f"site:{site.config.name}"]


@pytest.mark.parametrize(("earlier_failures", "notified"), [(4, False), (5, True), (6, False)])
def test_the_sixth_failure_since_the_last_success_notifies_once(
    tmp_path: Path, earlier_failures: int, notified: bool
) -> None:
    site = _site(tmp_path)
    now = datetime.now(UTC)
    _record(site, now - timedelta(hours=10), status="failed")
    _record(site, now - timedelta(hours=9))
    for attempt in range(earlier_failures):
        _record(site, now - timedelta(hours=attempt + 1), status="failed")
    task = BackupSiteTask(bench=site.bench, bench_root=site.bench.path, site=site.config.name, automatic=True)

    with patch("pilot.core.site.backups.SiteBackups.take", side_effect=OSError("disk full")), pytest.raises(SystemExit):
        task.backup()

    events = [notification.event for notification in site.bench.notifications.list(50)]
    assert ("backups_failing" in events) is notified


def test_the_bench_poll_reads_history_once_and_queues_only_due_sites(tmp_path: Path) -> None:
    site = _site(tmp_path)
    backed_up = site.bench.site("backed-up.localhost")
    backed_up.path.mkdir(parents=True)
    write_retention(backed_up.path / "site_config.json", BackupConfig(calendar_rotation=True))
    unenrolled = site.bench.site("manual.localhost")
    unenrolled.path.mkdir(parents=True)
    _record(backed_up, _NOW - timedelta(hours=2), with_files=True)

    with (
        patch("pilot.managers.task.control.TaskWorkerControl.wake"),
        patch.object(AuditLog, "entries", wraps=AuditLog(site.bench).entries) as entries,
    ):
        site.bench.backups.queue_scheduled(_NOW)

    entries.assert_called_once()
    task_dirs = list((site.bench.path / "tasks").glob("*/meta.json"))
    queued = [TaskStore(site.bench.path).read_metadata(path.parent.name)["args"]["site"] for path in task_dirs]
    assert queued == [site.config.name]
