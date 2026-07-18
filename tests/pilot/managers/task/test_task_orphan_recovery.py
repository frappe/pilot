from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import pilot.internal.tasks.worker as worker_module
from pilot.internal.tasks.process_identity import ProcessInspector
from pilot.internal.tasks.process import TaskProcessRecord
from pilot.managers.task.models import TaskStatus
from pilot.internal.tasks.store import TaskStore
from pilot.internal.tasks.worker import TaskWorker

RUNNING_TASK = "20260715-120000-111111"
QUEUED_TASK = "20260715-120000-222222"


def create_task(store: TaskStore, task_id: str) -> None:
    store.create_queued(
        {
            "task_id": task_id,
            "command": "build",
            "args": {},
            "command_argv": [sys.executable, "-c", "pass"],
            "queued_at": "2026-07-15T12:00:00+00:00",
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "failure": None,
            "bench_root": str(store.bench_root),
        }
    )


def start_owned_group(store: TaskStore) -> tuple[subprocess.Popen, list[str]]:
    launch_id = "orphan-launch-id"
    argv = [sys.executable, "-c", "import time; time.sleep(60)"]
    process = subprocess.Popen(
        argv,
        start_new_session=True,
        env={**os.environ, "BENCH_TASK_LAUNCH_ID": launch_id},
    )
    identity = ProcessInspector().capture(process.pid, argv, launch_id)
    record = TaskProcessRecord(RUNNING_TASK, argv, identity)
    store.write_process(RUNNING_TASK, process.pid, record.to_dict())
    return process, argv


@pytest.mark.parametrize("orphan_status", [TaskStatus.RUNNING, TaskStatus.KILLED])
def test_worker_waits_for_live_orphan_group_before_next_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    orphan_status: TaskStatus,
) -> None:
    store = TaskStore(tmp_path)
    create_task(store, RUNNING_TASK)
    create_task(store, QUEUED_TASK)
    store.transition(RUNNING_TASK, TaskStatus.QUEUED, TaskStatus.RUNNING)
    if orphan_status == TaskStatus.KILLED:
        store.transition(RUNNING_TASK, TaskStatus.RUNNING, TaskStatus.KILLED)
    orphan, _ = start_owned_group(store)
    completed = threading.Event()

    class Process:
        pid = 4322

        def wait(self, timeout: float | None = None) -> int:
            store.transition(QUEUED_TASK, TaskStatus.RUNNING, TaskStatus.SUCCESS)
            completed.set()
            return 0

    monkeypatch.setattr(
        worker_module.TaskProcess,
        "start",
        lambda self, task_id: Process(),
    )
    worker = TaskWorker(tmp_path)

    try:
        worker.start()
        assert not completed.wait(0.3)
        assert store.read_status(QUEUED_TASK) == TaskStatus.QUEUED

        os.killpg(orphan.pid, signal.SIGKILL)
        orphan.wait(timeout=5)
        assert completed.wait(2)
    finally:
        try:
            os.killpg(orphan.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        worker.request_drain()
        worker.join(2)

    if orphan_status == TaskStatus.RUNNING:
        assert store.read_status(RUNNING_TASK) == TaskStatus.FAILED
        assert store.read_metadata(RUNNING_TASK)["failure"] == {"code": "task_interrupted"}
    else:
        assert store.read_status(RUNNING_TASK) == TaskStatus.KILLED
    assert store.read_process(RUNNING_TASK) is None
    assert store.read_status(QUEUED_TASK) == TaskStatus.SUCCESS


def test_unknown_orphan_identity_blocks_new_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TaskStore(tmp_path)
    create_task(store, RUNNING_TASK)
    create_task(store, QUEUED_TASK)
    store.transition(RUNNING_TASK, TaskStatus.QUEUED, TaskStatus.RUNNING)
    task_dir = store.task_dir(RUNNING_TASK)
    (task_dir / "process.json").write_text("{}")
    started = threading.Event()
    monkeypatch.setattr(
        worker_module.TaskProcess,
        "start",
        lambda self, task_id: started.set(),
    )
    worker = TaskWorker(tmp_path)

    worker.start()
    assert not started.wait(0.3)
    worker.request_drain()
    worker.join(2)

    assert store.read_status(RUNNING_TASK) == TaskStatus.RUNNING
    assert store.read_status(QUEUED_TASK) == TaskStatus.QUEUED
    assert (task_dir / "process.json").exists()


def test_dead_orphan_is_failed_before_next_task_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TaskStore(tmp_path)
    create_task(store, RUNNING_TASK)
    create_task(store, QUEUED_TASK)
    store.transition(RUNNING_TASK, TaskStatus.QUEUED, TaskStatus.RUNNING)
    orphan, _ = start_owned_group(store)
    os.killpg(orphan.pid, signal.SIGKILL)
    orphan.wait(timeout=5)
    completed = threading.Event()

    class Process:
        pid = 4322

        def wait(self, timeout: float | None = None) -> int:
            store.transition(QUEUED_TASK, TaskStatus.RUNNING, TaskStatus.SUCCESS)
            completed.set()
            return 0

    monkeypatch.setattr(
        worker_module.TaskProcess,
        "start",
        lambda self, task_id: Process(),
    )
    worker = TaskWorker(tmp_path)

    worker.start()
    assert completed.wait(2)
    worker.request_drain()
    worker.join(2)

    assert store.read_status(RUNNING_TASK) == TaskStatus.FAILED
    assert store.read_metadata(RUNNING_TASK)["failure"] == {"code": "task_interrupted"}
