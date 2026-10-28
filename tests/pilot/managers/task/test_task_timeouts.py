from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

import pytest

from pilot.internal.atomic_file import exclusive_file_lock
from pilot.internal.tasks.models import TaskStatus
from pilot.internal.tasks.process import TaskProcess
from pilot.internal.tasks.process_identity import ProcessInspector, TaskProcessRecord
from pilot.internal.tasks.store import TaskStore
from pilot.internal.tasks.worker import TaskWorker
from pilot.managers.task.reader import TaskReader
from pilot.tasks import TaskRunner


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(0.02)


@pytest.mark.parametrize("adopt", [False, True])
def test_timeout_stops_detached_children_runs_cleanup_and_unblocks_queue(tmp_path, monkeypatch, adopt):
    monkeypatch.setattr(TaskWorker, "_wait_for_database", lambda self: None)
    site = tmp_path / "sites" / "test.local"
    site.mkdir(parents=True)
    config = site / "site_config.json"
    config.write_text('{"ssl": true}')
    child_pid = tmp_path / "child.pid"
    next_marker = tmp_path / "next.marker"
    script = (
        "import subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); "
        f"Path({str(child_pid)!r}).write_text(str(p.pid)); time.sleep(60)"
    )
    store = TaskStore(tmp_path)
    task_id, next_id = "20261026-000001-111111", "20261026-000002-222222"
    store.create_queued(
        {
            "task_id": task_id,
            "command": "build",
            "args": {},
            "command_argv": [sys.executable, "-c", script],
            "bench_root": str(tmp_path),
            "timeout_seconds": 2,
        },
        {
            "secrets.json": '{"password": "test-password"}',
            "callbacks.json": json.dumps(
                {"on_failure": {"operation": "disable-site-ssl", "args": {"site": "test.local"}}}
            ),
        },
    )
    store.create_queued(
        {
            "task_id": next_id,
            "command": "build",
            "args": {},
            "command_argv": [
                sys.executable,
                "-c",
                f"from pathlib import Path; Path({str(next_marker)!r}).touch()",
            ],
            "bench_root": str(tmp_path),
        }
    )
    orphan = None
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    worker = TaskWorker(tmp_path)
    try:
        if adopt:
            store.transition(
                task_id,
                TaskStatus.QUEUED,
                TaskStatus.RUNNING,
                {"started_at": (datetime.now(UTC) - timedelta(seconds=2)).isoformat()},
            )
            orphan = TaskProcess(tmp_path).start(task_id)
            wait_until(child_pid.exists)
        worker.start()
        wait_until(next_marker.exists)
        wait_until(lambda: store.read_status(next_id).is_terminal)
        assert json.loads(config.read_text()) == {"ssl": False}
        assert store.read_status(task_id) == "failed"
        task = TaskReader(tmp_path).read_task(task_id)
        assert task.failure.code == "task_timeout"
        assert "time limit" in task.failure.message
        assert unrelated.poll() is None
        assert not (store.task_dir(task_id) / "secrets.json").exists()
        assert not (store.task_dir(task_id) / "callbacks.json").exists()
        with contextlib.suppress(FileNotFoundError, ProcessLookupError):
            assert ProcessInspector()._read_process(int(child_pid.read_text())).state == "Z"
    finally:
        worker.request_drain()
        if worker._thread.ident is not None:
            worker.join(5)
        unrelated.kill()
        unrelated.wait(timeout=5)
        if orphan:
            orphan.wait(timeout=5)
        if child_pid.exists():
            with contextlib.suppress(ProcessLookupError):
                os.kill(int(child_pid.read_text()), 9)
    assert not worker.is_alive()


def test_submission_records_the_task_specific_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("pilot.managers.task.control.TaskWorkerControl.wake", lambda self: None)
    from pilot.tasks.backup_site import BackupSiteTask

    task_id = TaskRunner(tmp_path).run("backup-site", {"site": "test.local"})

    assert TaskStore(tmp_path).read_metadata(task_id)["timeout_seconds"] == BackupSiteTask.timeout_seconds


def test_a_blocked_callback_has_a_separate_deadline(tmp_path, monkeypatch):
    from pilot.internal.tasks import callbacks

    site = tmp_path / "sites" / "test.local"
    site.mkdir(parents=True)
    config = site / "site_config.json"
    config.write_text('{"ssl": true}')
    monkeypatch.setattr(callbacks, "CALLBACK_TIMEOUT_SECONDS", 0.3)

    started = time.monotonic()
    with exclusive_file_lock(config), pytest.raises(RuntimeError, match="timed out"):
        callbacks.run_bounded_callback(
            {"operation": "disable-site-ssl", "args": {"site": "test.local"}},
            {"bench_root": str(tmp_path)},
        )
    assert time.monotonic() - started < 3


@pytest.mark.parametrize("command", ["drop-site", "reinstall-site"])
def test_destructive_tasks_cannot_be_cancelled_after_start(tmp_path, command):
    from pilot.exceptions import TaskNotCancellableError

    store = TaskStore(tmp_path)
    task_id = "20261026-000001-111111"
    store.create_queued({"task_id": task_id, "command": command, "args": {}})
    store.transition(task_id, TaskStatus.QUEUED, TaskStatus.RUNNING)

    with pytest.raises(TaskNotCancellableError):
        TaskRunner(tmp_path).kill(task_id)
    assert store.read_status(task_id) == TaskStatus.RUNNING


def test_timeout_preserves_uncertain_process_ownership(tmp_path):
    store = TaskStore(tmp_path)
    task_id = "20261026-000001-111111"
    store.create_queued({"task_id": task_id, "command": "build", "timeout_seconds": 0})
    store.transition(task_id, TaskStatus.QUEUED, TaskStatus.RUNNING)
    (store.task_dir(task_id) / "process.json").write_text("{}")

    TaskProcess(tmp_path).enforce_timeout(task_id)

    assert store.read_status(task_id) == TaskStatus.RUNNING
    assert (store.task_dir(task_id) / "process.json").exists()


@pytest.mark.parametrize("metadata", [{"started_at": "invalid"}, {"timeout_seconds": "invalid"}])
def test_invalid_deadline_metadata_does_not_prevent_dead_process_recovery(tmp_path, metadata, caplog):
    store = TaskStore(tmp_path)
    task_id = "20261026-000001-111111"
    store.create_queued({"task_id": task_id, "command": "build", **metadata})
    store.transition(task_id, TaskStatus.QUEUED, TaskStatus.RUNNING)
    argv = [sys.executable, "-c", "import time; time.sleep(60)"]
    process = subprocess.Popen(
        argv, start_new_session=True, env={**os.environ, "BENCH_TASK_LAUNCH_ID": "dead-task"}
    )
    try:
        identity = ProcessInspector().capture(process.pid, argv, "dead-task")
        store.write_process(task_id, process.pid, TaskProcessRecord(task_id, argv, identity).to_dict())
    finally:
        process.kill()
        process.wait()

    assert TaskProcess(tmp_path).reconcile() is None
    assert store.read_status(task_id) == TaskStatus.FAILED
    assert "Cannot restore the execution deadline" in caplog.text


def test_a_dead_wrapper_does_not_leave_its_callback_running(tmp_path):
    site = tmp_path / "sites" / "test.local"
    site.mkdir(parents=True)
    config = site / "site_config.json"
    config.write_text('{"ssl": true}')
    task_id = "20261026-000001-111111"
    store = TaskStore(tmp_path)
    store.create_queued(
        {
            "task_id": task_id,
            "command": "build",
            "args": {},
            "bench_root": str(tmp_path),
            "command_argv": [sys.executable, "-c", "pass"],
        },
        {
            "callbacks.json": json.dumps(
                {"on_success": {"operation": "disable-site-ssl", "args": {"site": "test.local"}}}
            )
        },
    )
    store.transition(task_id, TaskStatus.QUEUED, TaskStatus.RUNNING)
    manager = TaskProcess(tmp_path)
    process = manager.start(task_id)
    record = manager.read(task_id)
    inspector = ProcessInspector()
    try:
        with exclusive_file_lock(config):
            wait_until(lambda: store.read_status(task_id) == TaskStatus.SUCCESS)
            wait_until(lambda: len(inspector.owned_pids(record.identity)) >= 2)
            process.kill()
            process.wait(timeout=5)
            assert manager.reconcile() == task_id
            wait_until(lambda: not inspector.owned_pids(record.identity))
            assert json.loads(config.read_text()) == {"ssl": True}
        manager.reconcile()
        assert store.read_status(task_id) == TaskStatus.SUCCESS
        assert json.loads(config.read_text()) == {"ssl": False}
        assert not (store.task_dir(task_id) / "callbacks.json").exists()
    finally:
        for pid in inspector.owned_pids(record.identity):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        process.wait(timeout=5)


def test_replayed_cleanup_is_stopped_before_replay_after_worker_crash(tmp_path):
    site = tmp_path / "sites" / "test.local"
    site.mkdir(parents=True)
    config = site / "site_config.json"
    config.write_text('{"ssl": true}')
    task_id = "20261026-000001-111111"
    store = TaskStore(tmp_path)
    store.create_queued(
        {"task_id": task_id, "command": "build", "args": {}, "bench_root": str(tmp_path)},
        {
            "callbacks.json": json.dumps(
                {"on_success": {"operation": "disable-site-ssl", "args": {"site": "test.local"}}}
            )
        },
    )
    store.transition(task_id, TaskStatus.QUEUED, TaskStatus.RUNNING)
    store.transition(task_id, TaskStatus.RUNNING, TaskStatus.SUCCESS)
    script = f"from pathlib import Path; from pilot.internal.tasks.process import TaskProcess; TaskProcess(Path({str(tmp_path)!r})).reconcile()"
    parent = subprocess.Popen([sys.executable, "-c", script])
    record = None
    inspector = ProcessInspector()
    try:
        with exclusive_file_lock(config):
            wait_until(lambda: store.read_process(task_id) is not None)
            record = TaskProcessRecord.from_dict(store.read_process(task_id))
            assert record.is_callback
            parent.kill()
            parent.wait(timeout=5)
            assert inspector.owned_pids(record.identity)
            manager = TaskProcess(tmp_path)
            assert manager.reconcile() == task_id
            wait_until(lambda: not inspector.owned_pids(record.identity))
            manager.interrupt(task_id)
            assert json.loads(config.read_text()) == {"ssl": True}
        assert manager.reconcile() is None
        assert store.read_status(task_id) == TaskStatus.SUCCESS
        assert json.loads(config.read_text()) == {"ssl": False}
        assert not (store.task_dir(task_id) / "callbacks.json").exists()
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=5)
        if record:
            for pid in inspector.owned_pids(record.identity):
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
