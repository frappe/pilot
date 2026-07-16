from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

import pilot.tasks.manager.worker as worker_module
from pilot.tasks.manager.task_state import TaskStatus
from pilot.tasks.manager.task_store import TaskStore
from pilot.tasks.manager.worker import TaskWorker
from pilot.tasks.manager.worker_state import WorkerIntent, WorkerStatus, WorkerStore


def enqueue(store: TaskStore, task_id: str, sequence: int) -> None:
    store.create_queued(
        {
            "task_id": task_id,
            "command": "build",
            "args": {},
            "command_argv": ["bench", "build"],
            "queue_sequence": sequence,
            "queued_at": "2026-07-15T12:00:00+00:00",
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
        }
    )


def test_worker_thread_runs_fifo_tasks_one_at_a_time(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TaskStore(tmp_path)
    first = "20260715-120000-111111"
    second = "20260715-120000-222222"
    enqueue(store, first, 1)
    enqueue(store, second, 2)
    events: list[tuple[str, str]] = []
    completed = threading.Event()

    class Process:
        def __init__(self, task_id: str, pid: int) -> None:
            self.task_id = task_id
            self.pid = pid

        def wait(self, timeout: float | None = None) -> int:
            events.append(("wait", self.task_id))
            store.transition(self.task_id, TaskStatus.RUNNING, TaskStatus.SUCCESS)
            if self.task_id == second:
                completed.set()
            return 0

    def start_process(self, task_id: str):
        if task_id == second:
            assert events == [("start", first), ("wait", first)]
        events.append(("start", task_id))
        return Process(task_id, 4000 + len(events))

    monkeypatch.setattr(worker_module.TaskProcess, "start", start_process)
    worker = TaskWorker(tmp_path)

    worker.start()
    assert completed.wait(2)
    worker.request_drain()
    worker.join(2)

    assert not worker.is_alive()
    assert events == [
        ("start", first),
        ("wait", first),
        ("start", second),
        ("wait", second),
    ]
    assert store.read_status(first) == TaskStatus.SUCCESS
    assert store.read_status(second) == TaskStatus.SUCCESS
    assert WorkerStore(tmp_path).read_pid() is None
    assert WorkerStore(tmp_path).read_state().status == WorkerStatus.STOPPED


def test_worker_delegates_claimed_task_to_task_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TaskStore(tmp_path)
    task_id = "20260715-120000-111111"
    enqueue(store, task_id, 1)
    completed = threading.Event()
    captured = []

    class Process:
        pid = 4321

        def wait(self, timeout: float | None = None) -> int:
            store.transition(task_id, TaskStatus.RUNNING, TaskStatus.SUCCESS)
            completed.set()
            return 0

    def start_process(self, requested_task_id: str):
        captured.append(requested_task_id)
        return Process()

    monkeypatch.setattr(worker_module.TaskProcess, "start", start_process)
    worker = TaskWorker(tmp_path)

    worker.start()
    assert completed.wait(2)
    worker.request_drain()
    worker.join(2)

    assert captured == [task_id]


def test_second_worker_thread_cannot_claim_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "20260715-120000-111111"
    enqueue(TaskStore(tmp_path), task_id, 1)
    lock = WorkerStore(tmp_path).try_acquire()
    assert lock is not None
    monkeypatch.setattr(
        worker_module.TaskProcess,
        "start",
        lambda self, task_id: pytest.fail("task process was started"),
    )
    worker = TaskWorker(tmp_path)

    try:
        worker.start()
        worker.join(2)
    finally:
        lock.release()

    assert not worker.is_alive()
    assert TaskStore(tmp_path).read_status(task_id) == TaskStatus.QUEUED


def test_idle_drain_does_not_claim_new_work(tmp_path: Path) -> None:
    worker = TaskWorker(tmp_path)
    state = WorkerStore(tmp_path)
    worker.start()
    wait_for_status(state, WorkerStatus.IDLE)

    worker.request_drain()
    enqueue(TaskStore(tmp_path), "20260715-120000-111111", 1)
    worker.wake()
    worker.join(2)

    assert not worker.is_alive()
    assert TaskStore(tmp_path).read_status("20260715-120000-111111") == TaskStatus.QUEUED


def test_running_drain_finishes_current_task_and_leaves_next_queued(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TaskStore(tmp_path)
    current = "20260715-120000-111111"
    queued = "20260715-120000-222222"
    enqueue(store, current, 1)
    started = threading.Event()
    finish = threading.Event()

    class Process:
        pid = 4321

        def wait(self, timeout: float | None = None) -> int:
            started.set()
            if not finish.wait(timeout):
                raise subprocess.TimeoutExpired("wrapper", timeout)
            store.transition(current, TaskStatus.RUNNING, TaskStatus.SUCCESS)
            return 0

    monkeypatch.setattr(worker_module.TaskProcess, "start", lambda self, task_id: Process())
    worker = TaskWorker(tmp_path)
    worker.start()
    assert started.wait(2)
    enqueue(store, queued, 2)

    worker.request_drain()
    wait_for_status(WorkerStore(tmp_path), WorkerStatus.DRAINING)
    finish.set()
    worker.join(2)

    assert not worker.is_alive()
    assert store.read_status(current) == TaskStatus.SUCCESS
    assert store.read_status(queued) == TaskStatus.QUEUED


def test_stopped_intent_survives_start_and_resumes_queued_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TaskStore(tmp_path)
    state = WorkerStore(tmp_path)
    task_id = "20260715-120000-111111"
    enqueue(store, task_id, 1)
    state.write_intent(WorkerIntent.STOPPED)
    completed = threading.Event()

    class Process:
        pid = 4321

        def wait(self, timeout: float | None = None) -> int:
            store.transition(task_id, TaskStatus.RUNNING, TaskStatus.SUCCESS)
            completed.set()
            return 0

    monkeypatch.setattr(worker_module.TaskProcess, "start", lambda self, task_id: Process())
    worker = TaskWorker(tmp_path)
    worker.start()
    wait_for_status(state, WorkerStatus.STOPPED)

    assert store.read_status(task_id) == TaskStatus.QUEUED
    assert worker.is_alive()

    state.write_intent(WorkerIntent.RUNNING)
    assert completed.wait(2)
    worker.request_drain()
    worker.join(2)

    assert store.read_status(task_id) == TaskStatus.SUCCESS


def test_competing_worker_threads_execute_task_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TaskStore(tmp_path)
    task_id = "20260715-120000-111111"
    enqueue(store, task_id, 1)
    starts = 0
    starts_lock = threading.Lock()
    completed = threading.Event()

    class Process:
        pid = 4321

        def wait(self, timeout: float | None = None) -> int:
            store.transition(task_id, TaskStatus.RUNNING, TaskStatus.SUCCESS)
            completed.set()
            return 0

    def start_process(self, requested_task_id: str):
        nonlocal starts
        with starts_lock:
            starts += 1
        return Process()

    monkeypatch.setattr(worker_module.TaskProcess, "start", start_process)
    workers = [TaskWorker(tmp_path), TaskWorker(tmp_path)]

    for worker in workers:
        worker.start()
    assert completed.wait(2)
    for worker in workers:
        worker.request_drain()
        worker.join(2)

    assert starts == 1
    assert store.read_status(task_id) == TaskStatus.SUCCESS


def test_enqueue_after_idle_scan_is_woken_without_lost_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TaskStore(tmp_path)
    task_id = "20260715-120000-111111"
    completed = threading.Event()

    class Process:
        pid = 4321

        def wait(self, timeout: float | None = None) -> int:
            store.transition(task_id, TaskStatus.RUNNING, TaskStatus.SUCCESS)
            completed.set()
            return 0

    monkeypatch.setattr(worker_module.TaskProcess, "start", lambda self, task_id: Process())
    worker = TaskWorker(tmp_path)
    worker.start()
    wait_for_status(WorkerStore(tmp_path), WorkerStatus.IDLE)

    enqueue(store, task_id, 1)
    worker.wake()
    assert completed.wait(2)
    worker.request_drain()
    worker.join(2)

    assert store.read_status(task_id) == TaskStatus.SUCCESS


def wait_for_status(store: WorkerStore, expected: WorkerStatus) -> None:
    for _ in range(100):
        state = store.read_state()
        if state is not None and state.status == expected:
            return
        threading.Event().wait(0.01)
    raise AssertionError(f"worker did not reach {expected.value}")
