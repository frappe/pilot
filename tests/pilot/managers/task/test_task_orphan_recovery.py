from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import pilot.internal.tasks.worker as worker_module
from pilot.internal.tasks.process import WRAPPER_MODULE, TaskProcess, TaskProcessRecord
from pilot.internal.tasks.process_identity import ProcessInspector
from pilot.internal.tasks.store import TaskStore
from pilot.internal.tasks.worker import TaskWorker
from pilot.managers.task.models import TaskStatus

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
        with contextlib.suppress(ProcessLookupError):
            os.killpg(orphan.pid, signal.SIGKILL)
        worker.request_drain()
        worker.join(2)

    if orphan_status == TaskStatus.RUNNING:
        assert store.read_status(RUNNING_TASK) == TaskStatus.FAILED
        assert store.read_metadata(RUNNING_TASK)["failure"] == {"code": "task_interrupted"}
    else:
        assert store.read_status(RUNNING_TASK) == TaskStatus.KILLED
    assert store.read_process(RUNNING_TASK) is None
    assert store.read_status(QUEUED_TASK) == TaskStatus.SUCCESS


def test_wrapper_crash_stops_detached_children_before_failing_task(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    ready = tmp_path / "child-ready"
    child_code = f"from pathlib import Path; import time; Path({str(ready)!r}).touch(); time.sleep(60)"
    command = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}], start_new_session=True); "
        "time.sleep(60)"
    )
    store.create_queued(
        {
            "task_id": RUNNING_TASK,
            "command": "build",
            "args": {},
            "command_argv": [sys.executable, "-c", command],
            "bench_root": str(tmp_path),
            "queued_at": "2026-07-15T12:00:00+00:00",
        }
    )
    worker = TaskWorker(tmp_path)
    processes = TaskProcess(tmp_path)
    record = None
    try:
        worker.start()
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        record = processes.read(RUNNING_TASK)
        assert record is not None
        os.kill(record.identity.pid, signal.SIGKILL)
        deadline = time.monotonic() + 5
        while store.read_status(RUNNING_TASK).is_active and time.monotonic() < deadline:
            time.sleep(0.01)
        assert store.read_status(RUNNING_TASK) == TaskStatus.FAILED
        assert not ProcessInspector().owned_pids(record.identity)
    finally:
        if record is not None:
            processes._signal(record, signal.SIGKILL)
        worker.request_drain()
        worker.join(2)


def start_wrapper_lookalike(task_dir: Path) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", WRAPPER_MODULE, str(task_dir)],
        start_new_session=True,
    )


@pytest.mark.parametrize("record", ["{}", "not json"])
def test_finished_task_with_a_corrupt_record_does_not_block_the_queue(tmp_path: Path, record: str) -> None:
    store = TaskStore(tmp_path)
    create_task(store, RUNNING_TASK)
    create_task(store, QUEUED_TASK)
    store.transition(RUNNING_TASK, TaskStatus.QUEUED, TaskStatus.RUNNING)
    store.transition(RUNNING_TASK, TaskStatus.RUNNING, TaskStatus.SUCCESS)
    (store.task_dir(RUNNING_TASK) / "process.json").write_text(record)

    assert TaskProcess(tmp_path).reconcile() is None
    assert not (store.task_dir(RUNNING_TASK) / "process.json").exists()
    assert store.read_status(RUNNING_TASK) == TaskStatus.SUCCESS
    assert TaskWorker(tmp_path)._claim_next() == QUEUED_TASK


def test_corrupt_recovery_record_preserves_a_live_callback(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    create_task(store, RUNNING_TASK)
    store.transition(RUNNING_TASK, TaskStatus.QUEUED, TaskStatus.RUNNING)
    store.transition(RUNNING_TASK, TaskStatus.RUNNING, TaskStatus.SUCCESS)
    task_dir = store.task_dir(RUNNING_TASK)
    (task_dir / "process.json").write_text("{}")
    callback = subprocess.Popen(
        [sys.executable, "-m", "pilot.internal.tasks.callbacks", str(task_dir)],
        stdin=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        assert TaskProcess(tmp_path).reconcile() == RUNNING_TASK
        assert callback.poll() is None
        assert (task_dir / "process.json").exists()
    finally:
        callback.kill()
        callback.wait(timeout=5)
        callback.stdin.close()

    assert TaskProcess(tmp_path).reconcile() is None
    assert store.read_status(RUNNING_TASK) == TaskStatus.SUCCESS


def test_unreadable_orphan_record_blocks_while_the_wrapper_lives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TaskStore(tmp_path)
    create_task(store, RUNNING_TASK)
    create_task(store, QUEUED_TASK)
    store.transition(RUNNING_TASK, TaskStatus.QUEUED, TaskStatus.RUNNING)
    task_dir = store.task_dir(RUNNING_TASK)
    (task_dir / "process.json").write_text("{}")
    wrapper = start_wrapper_lookalike(task_dir)
    started = threading.Event()
    monkeypatch.setattr(
        worker_module.TaskProcess,
        "start",
        lambda self, task_id: started.set(),
    )
    worker = TaskWorker(tmp_path)

    try:
        worker.start()
        assert not started.wait(0.3)
        worker.request_drain()
        worker.join(2)
    finally:
        wrapper.kill()
        wrapper.wait(timeout=5)

    assert store.read_status(RUNNING_TASK) == TaskStatus.RUNNING
    assert store.read_status(QUEUED_TASK) == TaskStatus.QUEUED
    assert (task_dir / "process.json").exists()


def test_unreadable_orphan_record_is_failed_when_no_wrapper_is_alive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TaskStore(tmp_path)
    create_task(store, RUNNING_TASK)
    create_task(store, QUEUED_TASK)
    store.transition(RUNNING_TASK, TaskStatus.QUEUED, TaskStatus.RUNNING)
    (store.task_dir(RUNNING_TASK) / "process.json").write_text("{}")
    completed = threading.Event()

    class Process:
        pid = 4323

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


def test_interrupt_fails_a_task_whose_process_record_is_gone(tmp_path: Path, monkeypatch) -> None:
    from pilot.exceptions import TaskNotFoundError

    processes = TaskProcess(tmp_path)
    transitions = []
    monkeypatch.setattr(processes, "read", lambda task_id: (_ for _ in ()).throw(TaskNotFoundError(task_id)))
    monkeypatch.setattr(processes._store, "transition", lambda *args: transitions.append(args[2]))
    monkeypatch.setattr(processes._store, "remove_private_files", lambda *args: None)

    processes.interrupt(RUNNING_TASK)

    assert transitions == [TaskStatus.FAILED]
