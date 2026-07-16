from __future__ import annotations

from pathlib import Path

from pilot.tasks.manager.worker_state import WorkerStore


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
