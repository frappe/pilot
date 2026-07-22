from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

import pilot.internal.tasks.files as task_files_module
from pilot.exceptions import TaskConflictError, TaskNotFoundError
from pilot.internal.tasks.store import TaskStore
from pilot.managers.task.models import TaskStatus

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
    real_replace = task_files_module.os.replace

    def fail_replace(source: Path, destination: Path) -> None:
        if Path(destination) == store.task_dir(TASK_ID):
            raise OSError("replace failed")
        real_replace(source, destination)

    monkeypatch.setattr(task_files_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        store.create_queued(metadata())

    assert not store.task_dir(TASK_ID).exists()
    assert [path for path in store.tasks_root.iterdir() if path.is_dir()] == []


def test_queue_sequence_recovery_ignores_symlinked_task_dirs(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    store.tasks_root.mkdir(parents=True)
    real_task = store.task_dir("20260715-120000-111111")
    real_task.mkdir()
    (real_task / "meta.json").write_text(json.dumps({**metadata(), "queue_sequence": 3}))

    outside = tmp_path / "outside-task"
    outside.mkdir()
    (outside / "meta.json").write_text(json.dumps({**metadata(), "queue_sequence": 99}))
    try:
        (store.tasks_root / "20260715-120000-222222").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    task_dir = store.create_queued({**metadata(), "task_id": "20260715-120000-333333"})

    assert json.loads((task_dir / "meta.json").read_text())["queue_sequence"] == 4


def test_queue_sequence_recovery_ignores_staged_task_dirs(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    store.tasks_root.mkdir(parents=True)
    staged_task = store.tasks_root / ".20260715-120000-staged.tmp"
    staged_task.mkdir()
    (staged_task / "meta.json").write_text(json.dumps({**metadata(), "queue_sequence": 99}))
    (staged_task / "status").write_text(TaskStatus.QUEUED.value)

    task_dir = store.create_queued({**metadata(), "task_id": "20260715-120000-333333"})

    assert json.loads((task_dir / "meta.json").read_text())["queue_sequence"] == 1
    assert staged_task.exists()


def test_queue_sequence_recovery_ignores_invalid_task_dirs(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    store.tasks_root.mkdir(parents=True)
    invalid_task = store.tasks_root / "not-a-task"
    invalid_task.mkdir()
    (invalid_task / "meta.json").write_text(json.dumps({**metadata(), "queue_sequence": 99}))
    (invalid_task / "status").write_text(TaskStatus.QUEUED.value)

    task_dir = store.create_queued({**metadata(), "task_id": "20260715-120000-333333"})

    assert json.loads((task_dir / "meta.json").read_text())["queue_sequence"] == 1
    assert invalid_task.exists()


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
    with pytest.raises(TaskNotFoundError, match="Invalid task ID"):
        store.read_status(".staged-task")
    with pytest.raises(TaskNotFoundError, match="Invalid task ID"):
        store.read_status("not-a-task")


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


def test_active_resource_rejects_another_task(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    store.create_queued(metadata(), resource_key="site:example.test")

    with pytest.raises(TaskConflictError, match="already using resource"):
        store.create_queued(
            {**metadata(), "task_id": "20260715-120001-bbccdd"},
            resource_key="site:example.test",
        )

    assert store.read_metadata(TASK_ID)["resource_key"] == "site:example.test"


def test_idempotent_replay_precedes_resource_conflict(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    store.create_idempotent_queued(
        metadata(),
        {},
        idempotency_digest="key-digest",
        request_fingerprint="request-digest",
        resource_key="site:example.test",
    )

    replay = store.create_idempotent_queued(
        {**metadata(), "task_id": "20260715-120001-bbccdd"},
        {},
        idempotency_digest="key-digest",
        request_fingerprint="request-digest",
        resource_key="site:example.test",
    )

    assert replay.created is False
    assert replay.task_id == TASK_ID


def test_active_resource_rejects_different_idempotent_task(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    store.create_idempotent_queued(
        metadata(),
        {},
        idempotency_digest="first-key",
        request_fingerprint="request-digest",
        resource_key="site:example.test",
    )

    with pytest.raises(TaskConflictError, match="already using resource"):
        store.create_idempotent_queued(
            {**metadata(), "task_id": "20260715-120001-bbccdd"},
            {},
            idempotency_digest="second-key",
            request_fingerprint="request-digest",
            resource_key="site:example.test",
        )


def test_terminal_task_releases_resource(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    store.create_queued(metadata(), resource_key="site:example.test")
    store.transition(TASK_ID, TaskStatus.QUEUED, TaskStatus.KILLED)
    retry_id = "20260715-120001-bbccdd"

    store.create_queued(
        {**metadata(), "task_id": retry_id},
        resource_key="site:example.test",
    )

    assert store.read_metadata(retry_id)["resource_key"] == "site:example.test"


@pytest.mark.parametrize("pending_file", ["callbacks.json", "process.json"])
def test_terminal_task_holds_resource_while_cleanup_is_pending(
    tmp_path: Path,
    pending_file: str,
) -> None:
    store = TaskStore(tmp_path)
    store.create_queued(
        metadata(),
        {pending_file: "{}"},
        resource_key="site:example.test",
    )
    store.transition(TASK_ID, TaskStatus.QUEUED, TaskStatus.KILLED)
    retry_metadata = {**metadata(), "task_id": "20260715-120001-bbccdd"}

    with pytest.raises(TaskConflictError, match="already using resource"):
        store.create_queued(
            retry_metadata,
            resource_key="site:example.test",
        )

    store.remove_private_files(TASK_ID, pending_file)
    store.create_queued(retry_metadata, resource_key="site:example.test")


def test_retention_preserves_task_with_pending_callback(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    store.create_queued(metadata(), {"callbacks.json": "{}"})
    store.transition(TASK_ID, TaskStatus.QUEUED, TaskStatus.KILLED)

    assert store.purge_terminal(0) == []
    assert store.task_dir(TASK_ID).exists()


def test_retention_deletes_only_oldest_terminal_tasks(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    queued_id = "20260715-120000-111111"
    running_id = "20260715-120000-222222"
    enqueue_metadata = {**metadata(), "task_id": queued_id}
    store.create_queued(enqueue_metadata)
    store.create_queued({**metadata(), "task_id": running_id})
    store.transition(running_id, TaskStatus.QUEUED, TaskStatus.RUNNING)

    terminal_records = [
        ("20260715-120000-ffffff", "success", "2026-07-15T12:00:01+00:00"),
        ("20260715-120000-000000", "failed", "2026-07-15T12:00:03+00:00"),
        ("20260715-120000-aaaaaa", "killed", "2026-07-15T12:00:02+00:00"),
    ]
    for task_id, status, finished_at in terminal_records:
        task_dir = store.task_dir(task_id)
        task_dir.mkdir()
        record = {**metadata(), "task_id": task_id, "finished_at": finished_at}
        (task_dir / "meta.json").write_text(json.dumps(record))
        (task_dir / "status").write_text(status)

    unknown = store.tasks_root / "unknown-record"
    unknown.mkdir()
    (unknown / "meta.json").write_text("{}")
    (unknown / "status").write_text("future-state")
    missing_status = store.tasks_root / "missing-status"
    missing_status.mkdir()
    (missing_status / "meta.json").write_text("{}")

    deleted = store.purge_terminal(1)

    assert deleted == ["20260715-120000-ffffff", "20260715-120000-aaaaaa"]
    assert store.task_dir("20260715-120000-000000").exists()
    assert store.task_dir(queued_id).exists()
    assert store.task_dir(running_id).exists()
    assert unknown.exists()
    assert missing_status.exists()


def test_retention_keeps_terminal_task_with_live_process_record(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    protected = "20260715-120000-111111"
    removable = "20260715-120000-222222"
    for task_id in (protected, removable):
        task_dir = store.task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "meta.json").write_text(json.dumps({**metadata(), "task_id": task_id}))
        (task_dir / "status").write_text("killed")
    (store.task_dir(protected) / "process.json").write_text("{}")

    deleted = store.purge_terminal(0)

    assert deleted == [removable]
    assert store.task_dir(protected).exists()
