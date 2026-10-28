from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pilot.internal.tasks.worker as worker_module
from pilot.internal.tasks.worker_state import WorkerIntent, WorkerStore
from pilot.managers.task.control import TaskWorkerControl


def test_worker_control_start_request_persists_intent_and_wakes_worker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker_registry = Mock()
    monkeypatch.setattr(worker_module, "task_workers", worker_registry)
    WorkerStore(tmp_path).write_intent(WorkerIntent.STOPPED)

    TaskWorkerControl(tmp_path).request_start()

    assert WorkerStore(tmp_path).read_intent() == WorkerIntent.RUNNING
    worker_registry.wake.assert_called_once_with(tmp_path)


def test_worker_control_stop_request_persists_intent_and_wakes_worker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker_registry = Mock()
    monkeypatch.setattr(worker_module, "task_workers", worker_registry)

    TaskWorkerControl(tmp_path).request_stop()

    assert WorkerStore(tmp_path).read_intent() == WorkerIntent.STOPPED
    worker_registry.wake.assert_called_once_with(tmp_path)


def test_worker_control_starts_background_worker_and_signal_handlers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker_registry = Mock()
    monkeypatch.setattr(worker_module, "task_workers", worker_registry)

    TaskWorkerControl(tmp_path).start_background_worker()

    worker_registry.start.assert_called_once_with(tmp_path)
    worker_registry.install_signal_handlers.assert_called_once_with()


def test_worker_control_wake_uses_the_local_worker_when_there_is_one(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(worker_module, "task_workers", Mock(**{"wake.return_value": True}))

    with patch("pilot.managers.processes.local.ProcessManager.for_bench") as for_bench:
        TaskWorkerControl(tmp_path).wake()

    for_bench.assert_not_called()


def test_worker_control_wake_starts_the_admin_without_a_local_worker(tmp_path: Path, monkeypatch) -> None:
    """A CLI or task process has no worker. The Admin runs it, and it can be stopped on idle."""
    monkeypatch.setattr(worker_module, "task_workers", Mock(**{"wake.return_value": False}))

    with (
        patch("pilot.core.bench.Bench") as bench,
        patch("pilot.managers.processes.local.ProcessManager.for_bench") as for_bench,
    ):
        TaskWorkerControl(tmp_path).request_start()

    bench.assert_called_once_with(tmp_path)
    for_bench.assert_called_once_with(bench.return_value)
    for_bench.return_value.wake_admin.assert_called_once_with()


def _worker_with_database(tmp_path: Path, monkeypatch, ready: list[bool]):
    from pilot.core.bench.runtime import BenchRuntime
    from pilot.internal.tasks import worker as worker_module_internal

    events: list[str] = []
    monkeypatch.setattr("pilot.core.bench.Bench", lambda root: Mock(is_initialized=True))
    monkeypatch.setattr(
        BenchRuntime, "is_database_ready", property(lambda self: events.append("check") or ready.pop(0))
    )
    monkeypatch.setattr(worker_module_internal, "_DATABASE_POLL_SECONDS", 0.01)
    monkeypatch.setattr(worker_module_internal.TaskWorker, "_work", lambda self, pid: events.append("work"))
    return worker_module_internal.TaskWorker(tmp_path), events


def test_the_worker_waits_for_the_database_before_its_first_task(tmp_path: Path, monkeypatch) -> None:
    """At boot the Admin can start before MariaDB, and a task run then would fail."""
    worker, events = _worker_with_database(tmp_path, monkeypatch, [False, False, True])

    worker.start()
    worker.join(5)

    assert events == ["check", "check", "check", "work"]


def test_a_drain_ends_the_database_wait(tmp_path: Path, monkeypatch) -> None:
    """Stopping the Admin must not wait for a database that is down."""
    worker, events = _worker_with_database(tmp_path, monkeypatch, [False] * 1000)

    worker.request_drain()
    worker.start()
    worker.join(5)

    assert not worker.is_alive()
    assert events == ["check", "work"]
