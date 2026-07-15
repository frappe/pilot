from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

import admin.backend.tasks.manager.task_store as task_store_module
from admin.backend.tasks.manager.task_state import TaskStatus
from admin.backend.tasks.manager.task_store import TaskStore
from pilot.exceptions import TaskConflictError, TaskNotFoundError

TASK_ID = "20260715-120000-aabbcc"


def metadata() -> dict:
    return {
        "task_id": TASK_ID,
        "command": "build",
        "args": {},
        "queued_at": "2026-07-15T12:00:00+00:00",
        "started_at": None,
        "finished_at": None,
        "exit_code": None,
    }


def test_create_queued_publishes_complete_private_task(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)

    task_dir = store.create_queued(
        metadata(),
        {"secrets.json": '{"token":"secret"}', "callbacks.json": "{}"},
    )

    stored_metadata = store.read_metadata(TASK_ID)
    assert stored_metadata == {**metadata(), "queue_sequence": 1}
    assert store.read_status(TASK_ID) == TaskStatus.QUEUED
    assert json.loads((task_dir / "secrets.json").read_text()) == {"token": "secret"}
    assert stat.S_IMODE(task_dir.stat().st_mode) == 0o700
    for name in ("meta.json", "status", "secrets.json", "callbacks.json"):
        assert stat.S_IMODE((task_dir / name).stat().st_mode) == 0o600
    assert stat.S_IMODE((store.tasks_root / "queue-sequence").stat().st_mode) == 0o600


def test_create_failure_leaves_no_visible_or_staged_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = TaskStore(tmp_path)
    real_replace = task_store_module.os.replace

    def fail_replace(source: Path, destination: Path) -> None:
        if Path(destination) == store.task_dir(TASK_ID):
            raise OSError("replace failed")
        real_replace(source, destination)

    monkeypatch.setattr(task_store_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        store.create_queued(metadata())

    assert not store.task_dir(TASK_ID).exists()
    assert [path for path in store.tasks_root.iterdir() if path.is_dir()] == []


def test_transition_updates_metadata_before_status(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    store.create_queued(metadata())

    changed = store.transition(
        TASK_ID,
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        {"started_at": "2026-07-15T12:00:01+00:00"},
    )

    assert changed is True
    assert store.read_status(TASK_ID) == TaskStatus.RUNNING
    assert store.read_metadata(TASK_ID)["started_at"] == "2026-07-15T12:00:01+00:00"


def test_transition_compare_and_set_preserves_cancellation(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    store.create_queued(metadata())
    store.transition(TASK_ID, TaskStatus.QUEUED, TaskStatus.RUNNING)
    store.transition(TASK_ID, TaskStatus.RUNNING, TaskStatus.KILLED)

    changed = store.transition(
        TASK_ID,
        TaskStatus.RUNNING,
        TaskStatus.SUCCESS,
        {"exit_code": 0},
    )

    assert changed is False
    assert store.read_status(TASK_ID) == TaskStatus.KILLED
    assert store.read_metadata(TASK_ID)["exit_code"] is None


def test_transition_rejects_invalid_lifecycle_change(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    store.create_queued(metadata())

    with pytest.raises(ValueError, match="Invalid task transition"):
        store.transition(TASK_ID, TaskStatus.QUEUED, TaskStatus.SUCCESS)


def test_store_writes_pid_and_removes_private_files(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    task_dir = store.create_queued(metadata(), {"secrets.json": "{}"})

    store.write_pid(TASK_ID, 4321)
    store.remove_private_files(TASK_ID, "secrets.json")

    assert store.read_pid(TASK_ID) == 4321
    assert not (task_dir / "secrets.json").exists()
    assert stat.S_IMODE((task_dir / "pid").stat().st_mode) == 0o600


def test_store_rejects_missing_and_unsafe_task_paths(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)

    with pytest.raises(TaskNotFoundError, match="Task not found"):
        store.read_status(TASK_ID)
    with pytest.raises(TaskNotFoundError, match="Invalid task ID"):
        store.read_status("../outside")


def test_active_idempotent_submission_returns_existing_task(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    first = store.create_idempotent_queued(
        metadata(),
        {},
        idempotency_digest="key-digest",
        request_fingerprint="request-digest",
    )
    duplicate_metadata = {**metadata(), "task_id": "20260715-120001-bbccdd"}

    duplicate = store.create_idempotent_queued(
        duplicate_metadata,
        {},
        idempotency_digest="key-digest",
        request_fingerprint="request-digest",
    )

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.task_id == TASK_ID
    assert not store.task_dir(duplicate_metadata["task_id"]).exists()


def test_active_idempotency_key_rejects_another_request(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    store.create_idempotent_queued(
        metadata(),
        {},
        idempotency_digest="key-digest",
        request_fingerprint="first-request",
    )

    with pytest.raises(TaskConflictError, match="another active task"):
        store.create_idempotent_queued(
            {**metadata(), "task_id": "20260715-120001-bbccdd"},
            {},
            idempotency_digest="key-digest",
            request_fingerprint="different-request",
        )


def test_terminal_task_does_not_block_idempotent_retry(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    store.create_idempotent_queued(
        metadata(),
        {},
        idempotency_digest="key-digest",
        request_fingerprint="request-digest",
    )
    store.transition(TASK_ID, TaskStatus.QUEUED, TaskStatus.KILLED)
    retry_id = "20260715-120001-bbccdd"

    retry = store.create_idempotent_queued(
        {**metadata(), "task_id": retry_id},
        {},
        idempotency_digest="key-digest",
        request_fingerprint="request-digest",
    )

    assert retry.created is True
    assert retry.task_id == retry_id
