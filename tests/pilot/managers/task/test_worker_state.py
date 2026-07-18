from __future__ import annotations

import stat
from pathlib import Path

import pytest

from pilot.internal.tasks.worker_state import (
    WorkerIntent,
    WorkerState,
    WorkerStatus,
    WorkerStore,
)


def test_worker_store_creates_private_worker_files(tmp_path: Path) -> None:
    store = WorkerStore(tmp_path)

    store.write_pid(4321)
    store.write_intent(WorkerIntent.RUNNING)
    written = store.write_state(WorkerStatus.RUNNING, 4321, "task-id")

    assert store.read_pid() == 4321
    assert store.read_state() == written
    assert stat.S_IMODE(store.tasks_root.stat().st_mode) == 0o700
    for path in (store.lock_path, store.pid_path, store.state_path, store.intent_path):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_worker_pid_can_be_cleared(tmp_path: Path) -> None:
    store = WorkerStore(tmp_path)
    store.write_pid(4321)

    store.write_pid(None)

    assert store.read_pid() is None


@pytest.mark.parametrize("status", list(WorkerStatus))
def test_worker_state_round_trips_every_status(
    tmp_path: Path,
    status: WorkerStatus,
) -> None:
    store = WorkerStore(tmp_path)

    written = store.write_state(status, 1234)

    assert isinstance(written, WorkerState)
    assert store.read_state() == written


def test_missing_worker_state_returns_none(tmp_path: Path) -> None:
    store = WorkerStore(tmp_path)

    assert store.read_pid() is None
    assert store.read_state() is None
    assert store.read_intent() == WorkerIntent.RUNNING


@pytest.mark.parametrize("intent", list(WorkerIntent))
def test_worker_intent_round_trips(tmp_path: Path, intent: WorkerIntent) -> None:
    store = WorkerStore(tmp_path)

    store.write_intent(intent)

    assert store.read_intent() == intent


def test_unknown_worker_status_is_rejected(tmp_path: Path) -> None:
    store = WorkerStore(tmp_path)
    store.ensure_layout()
    store.state_path.write_text(
        '{"status":"lost","pid":1,"current_task_id":null,"updated_at":"2026-07-15T12:00:00+00:00"}'
    )

    with pytest.raises(ValueError, match="lost"):
        store.read_state()


def test_only_one_worker_lock_can_be_owned(tmp_path: Path) -> None:
    first_store = WorkerStore(tmp_path)
    second_store = WorkerStore(tmp_path)

    first = first_store.try_acquire()
    second = second_store.try_acquire()

    assert first is not None
    assert second is None
    first.release()


def test_worker_lock_can_be_reacquired_after_release(tmp_path: Path) -> None:
    store = WorkerStore(tmp_path)
    first = store.try_acquire()
    assert first is not None
    first.release()

    second = store.try_acquire()

    assert second is not None
    second.release()


def test_releasing_worker_lock_is_idempotent(tmp_path: Path) -> None:
    lock = WorkerStore(tmp_path).try_acquire()
    assert lock is not None

    lock.release()
    lock.release()
