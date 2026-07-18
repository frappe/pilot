from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

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
