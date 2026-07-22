from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pilot.internal.tasks.queue import TaskQueue
from pilot.internal.tasks.store import TaskStore
from pilot.managers.task.models import TaskStatus


def task_metadata(task_id: str) -> dict:
    return {
        "task_id": task_id,
        "command": "build",
        "args": {},
        "queued_at": "2026-07-15T12:00:00+00:00",
        "started_at": None,
        "finished_at": None,
        "exit_code": None,
    }


def enqueue(store: TaskStore, task_id: str) -> None:
    store.create_queued(task_metadata(task_id))


def test_queue_preserves_submission_order_with_same_timestamp(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    task_ids = [
        "20260715-120000-ffffff",
        "20260715-120000-000000",
        "20260715-120000-aaaaaa",
    ]
    for task_id in task_ids:
        enqueue(store, task_id)

    assert TaskQueue(tmp_path).queued_task_ids() == task_ids
    assert TaskQueue(tmp_path).positions() == {
        task_ids[0]: 1,
        task_ids[1]: 2,
        task_ids[2]: 3,
    }


def test_empty_queue_does_not_require_tasks_directory(tmp_path: Path) -> None:
    assert TaskQueue(tmp_path).queued_task_ids() == []
    assert TaskQueue(tmp_path).positions() == {}
    assert TaskQueue(tmp_path).claim_next() is None


def test_claim_next_transitions_oldest_queued_task(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    first = "20260715-120000-ffffff"
    second = "20260715-120000-000000"
    enqueue(store, first)
    enqueue(store, second)

    claimed = TaskQueue(tmp_path).claim_next()

    assert claimed == first
    assert store.read_status(first) == TaskStatus.RUNNING
    assert store.read_metadata(first)["started_at"] is not None
    assert TaskQueue(tmp_path).queued_task_ids() == [second]


def test_queue_state_survives_new_instances(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    first = "20260715-120000-111111"
    second = "20260715-120000-222222"
    enqueue(store, first)
    enqueue(store, second)

    assert TaskQueue(tmp_path).claim_next() == first
    assert TaskQueue(tmp_path).claim_next() == second
    assert TaskQueue(tmp_path).claim_next() is None


def test_competing_claims_choose_distinct_tasks(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    task_ids = ["20260715-120000-111111", "20260715-120000-222222"]
    for task_id in task_ids:
        enqueue(store, task_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        claimed = list(executor.map(lambda _: TaskQueue(tmp_path).claim_next(), range(2)))

    assert sorted(claimed) == sorted(task_ids)
    assert all(store.read_status(task_id) == TaskStatus.RUNNING for task_id in task_ids)


def test_queue_skips_terminal_tasks(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    killed = "20260715-120000-111111"
    queued = "20260715-120000-222222"
    enqueue(store, killed)
    enqueue(store, queued)
    store.transition(killed, TaskStatus.QUEUED, TaskStatus.KILLED)

    assert TaskQueue(tmp_path).queued_task_ids() == [queued]
    assert TaskQueue(tmp_path).claim_next() == queued


def test_queue_ignores_staged_task_dirs(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    queued = "20260715-120000-111111"
    staged = "20260715-120000-222222"
    enqueue(store, queued)

    staged_dir = store.tasks_root / f".{staged}.tmp"
    staged_dir.mkdir()
    (staged_dir / "meta.json").write_text(json.dumps(task_metadata(staged)))
    (staged_dir / "status").write_text(TaskStatus.QUEUED.value)

    assert TaskQueue(tmp_path).queued_task_ids() == [queued]
    assert TaskQueue(tmp_path).claim_next() == queued
    assert staged_dir.exists()
    assert TaskQueue(tmp_path).claim_next() is None


def test_queue_ignores_invalid_task_dirs(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    queued = "20260715-120000-111111"
    enqueue(store, queued)

    invalid_dir = store.tasks_root / "not-a-task"
    invalid_dir.mkdir()
    (invalid_dir / "meta.json").write_text(json.dumps(task_metadata("20260715-120000-222222")))
    (invalid_dir / "status").write_text(TaskStatus.QUEUED.value)

    assert TaskQueue(tmp_path).queued_task_ids() == [queued]
    assert TaskQueue(tmp_path).claim_next() == queued
    assert invalid_dir.exists()
    assert TaskQueue(tmp_path).claim_next() is None


def test_queue_ignores_symlinked_task_dirs(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    queued = "20260715-120000-111111"
    linked = "20260715-120000-222222"
    enqueue(store, queued)

    outside = tmp_path / "outside-task"
    outside.mkdir()
    (outside / "meta.json").write_text(json.dumps(task_metadata(linked)))
    (outside / "status").write_text(TaskStatus.QUEUED.value)
    try:
        (store.tasks_root / linked).symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    assert TaskQueue(tmp_path).queued_task_ids() == [queued]
