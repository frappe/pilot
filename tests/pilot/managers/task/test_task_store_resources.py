from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from pilot.exceptions import TaskConflictError
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


def test_active_resource_rejects_another_task(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    store.create_queued(metadata(), resource_key="site:example.test")

    with pytest.raises(TaskConflictError, match="already using resource"):
        store.create_queued(
            {**metadata(), "task_id": "20260715-120001-bbccdd"},
            resource_key="site:example.test",
        )

    assert store.read_metadata(TASK_ID)["resource_keys"] == ["site:example.test"]


def test_direct_site_reservation_uses_lowercase_and_releases_on_error(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)

    with pytest.raises(RuntimeError, match="drop failed"), store.reserve_direct_site("Example.Test"):
        raise RuntimeError("drop failed")

    store.create_queued(metadata(), resource_key="site:example.test")
    with (
        pytest.raises(TaskConflictError, match=r"site:example\.test"),
        store.reserve_direct_site("Example.Test"),
    ):
        pytest.fail("reservation must reject the active task")


@pytest.mark.parametrize(
    "command",
    [
        "backup-site",
        "build",
        "clear-cache",
        "delete-backup",
        "drop-site",
        "get-and-install-app",
        "install-app",
        "migrate",
        "migration-backup",
        "reinstall-site",
        "rename-site",
        "restore-site",
        "revert-site",
        "setup-letsencrypt",
        "uninstall-app",
    ],
)
@pytest.mark.parametrize("status", [TaskStatus.QUEUED, TaskStatus.RUNNING])
def test_direct_site_reservation_rejects_unkeyed_site_work(
    tmp_path: Path, command: str, status: TaskStatus
) -> None:
    store = TaskStore(tmp_path)
    store.create_queued({**metadata(), "command": command, "args": {"site": "Example.Test"}})
    if status == TaskStatus.RUNNING:
        store.transition(TASK_ID, TaskStatus.QUEUED, status)

    with (
        pytest.raises(TaskConflictError, match=r"site:example\.test"),
        store.reserve_direct_site("example.test"),
    ):
        pytest.fail("drop must reject active site work")


@pytest.mark.parametrize("command", ["new-site", "new-site-from-backup"])
@pytest.mark.parametrize("status", [TaskStatus.QUEUED, TaskStatus.RUNNING])
def test_direct_site_reservation_rejects_unkeyed_site_creation(
    tmp_path: Path, command: str, status: TaskStatus
) -> None:
    store = TaskStore(tmp_path)
    store.create_queued({**metadata(), "command": command, "args": {"name": "Example.Test"}})
    if status == TaskStatus.RUNNING:
        store.transition(TASK_ID, TaskStatus.QUEUED, status)

    with (
        pytest.raises(TaskConflictError, match=r"site:example\.test"),
        store.reserve_direct_site("example.test"),
    ):
        pytest.fail("drop must reject active site creation")


@pytest.mark.parametrize(("command", "field"), [("restore-site", "source_site"), ("rename-site", "new_name")])
@pytest.mark.parametrize("status", [TaskStatus.QUEUED, TaskStatus.RUNNING])
def test_direct_site_reservation_rejects_secondary_site_work(
    tmp_path: Path, command: str, field: str, status: TaskStatus
) -> None:
    store = TaskStore(tmp_path)
    store.create_queued(
        {**metadata(), "command": command, "args": {"site": "other.test", field: "Example.Test"}}
    )
    if status == TaskStatus.RUNNING:
        store.transition(TASK_ID, TaskStatus.QUEUED, status)

    with (
        pytest.raises(TaskConflictError, match=r"site:example\.test"),
        store.reserve_direct_site("example.test"),
    ):
        pytest.fail("drop must reject work using the site as a source or new name")


@pytest.mark.parametrize("command", ["restore-site", "reinstall-site", "drop-site"])
def test_direct_site_reservation_rejects_new_site_task(tmp_path: Path, command: str) -> None:
    store = TaskStore(tmp_path)

    with (
        store.reserve_direct_site("example.test"),
        pytest.raises(TaskConflictError, match=r"site:example\.test"),
    ):
        store.create_queued({**metadata(), "command": command, "args": {"site": "Example.Test"}})

    assert not store.task_dir(TASK_ID).exists()


def test_direct_site_reservation_allows_other_site_task(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    store.create_queued({**metadata(), "command": "restore-site", "args": {"site": "other.test"}})

    with store.reserve_direct_site("example.test"):
        pass


def test_direct_site_reservation_rejects_multi_site_app_work(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    store.create_queued(
        {
            **metadata(),
            "command": "get-and-install-app",
            "args": {"site": "", "sites": ["Other.Test", "Example.Test"]},
        }
    )

    with (
        pytest.raises(TaskConflictError, match=r"site:example\.test"),
        store.reserve_direct_site("example.test"),
    ):
        pytest.fail("drop must reject a task that includes the site")


def test_direct_site_reservation_rejects_new_unkeyed_work_and_inline_write(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    backup = {**metadata(), "command": "backup-site", "args": {"site": "example.test"}}
    install = {**metadata(), "command": "install-app", "args": {"site": "Example.Test"}}

    with store.reserve_direct_site("Example.Test"):
        with pytest.raises(TaskConflictError, match=r"site:example\.test"):
            store.create_queued(backup)
        with pytest.raises(TaskConflictError, match=r"site:example\.test"):
            store.create_idempotent_queued(
                install, {}, idempotency_digest="key-digest", request_fingerprint="request-digest"
            )
        with (
            pytest.raises(TaskConflictError, match=r"site:example\.test"),
            store.reserve_inline_site_write("example.test"),
        ):
            pytest.fail("inline write must not start during drop")

    assert not store.task_dir(TASK_ID).exists()
    store.create_queued(backup)


def test_inline_site_writes_and_unkeyed_tasks_remain_concurrent(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    entered = Event()
    release = Event()
    backup = {**metadata(), "command": "backup-site", "args": {"site": "example.test"}}
    install = {
        **metadata(),
        "task_id": "20260715-120001-bbccdd",
        "command": "install-app",
        "args": {"site": "example.test"},
    }

    def hold_second_inline_write() -> None:
        with TaskStore(tmp_path).reserve_inline_site_write("example.test"):
            entered.set()
            assert release.wait(10)

    with store.reserve_inline_site_write("Example.Test"), ThreadPoolExecutor(max_workers=1) as executor:
        second = executor.submit(hold_second_inline_write)
        try:
            assert entered.wait(10)
            store.create_queued(backup)
            store.create_queued(install)
            with (
                pytest.raises(TaskConflictError, match=r"site:example\.test"),
                store.reserve_direct_site("example.test"),
            ):
                pytest.fail("drop must not start during an inline write")
        finally:
            release.set()
        second.result(timeout=10)

    assert store.read_status(TASK_ID) == TaskStatus.QUEUED
    assert store.read_status(install["task_id"]) == TaskStatus.QUEUED


def test_unkeyed_site_idempotent_replay_remains_available(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    backup = {**metadata(), "command": "backup-site", "args": {"site": "example.test"}}

    with store.reserve_inline_site_write("example.test"):
        first = store.create_idempotent_queued(
            backup, {}, idempotency_digest="key-digest", request_fingerprint="request-digest"
        )
        replay = store.create_idempotent_queued(
            {**backup, "task_id": "20260715-120001-bbccdd"},
            {},
            idempotency_digest="key-digest",
            request_fingerprint="request-digest",
        )

    assert first.created is True
    assert replay.created is False
    assert replay.task_id == first.task_id


def test_inline_site_write_releases_lock_on_error(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    with pytest.raises(RuntimeError, match="enable failed"), store.reserve_inline_site_write("example.test"):
        raise RuntimeError("enable failed")

    with store.reserve_direct_site("example.test"):
        pass


def test_direct_site_lock_releases_after_process_exit(tmp_path: Path) -> None:
    code = (
        "import os, sys\n"
        "from pathlib import Path\n"
        "from pilot.internal.tasks.store import TaskStore\n"
        "with TaskStore(Path(sys.argv[1])).reserve_direct_site('example.test'):\n"
        "    os._exit(0)\n"
    )
    subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=True)

    with TaskStore(tmp_path).reserve_inline_site_write("example.test"):
        pass


def test_active_resource_can_be_handed_to_successor(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    store.create_queued(metadata(), resource_key=["bench:update", "site:example.test"])
    store.transition(TASK_ID, TaskStatus.QUEUED, TaskStatus.RUNNING)
    successor_id = "20260715-120001-bbccdd"

    store.create_queued(
        {**metadata(), "task_id": successor_id},
        resource_key=["bench:update", "site:example.test"],
        resource_handoff_from=TASK_ID,
    )

    assert store.read_metadata(successor_id)["resource_keys"] == [
        "bench:update",
        "site:example.test",
    ]


def test_resource_handoff_does_not_ignore_another_owner(tmp_path: Path) -> None:
    store = TaskStore(tmp_path)
    store.create_queued(metadata(), resource_key="site:example.test")

    with pytest.raises(TaskConflictError, match="already using resource"):
        store.create_queued(
            {**metadata(), "task_id": "20260715-120001-bbccdd"},
            resource_key="site:example.test",
            resource_handoff_from="not-the-active-task",
        )


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

    assert store.read_metadata(retry_id)["resource_keys"] == ["site:example.test"]


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


# --- hostname claims are host-wide -------------------------------------------
# Benches on one host share one nginx and one /etc/letsencrypt, so a `host:` key
# has to conflict across benches; every other key stays bench-local.

OTHER_ID = "20260715-120001-bbccdd"


def _benches(tmp_path: Path) -> tuple[TaskStore, TaskStore]:
    return TaskStore(tmp_path / "b1"), TaskStore(tmp_path / "b2")


def test_a_hostname_held_on_one_bench_blocks_another(tmp_path: Path) -> None:
    first, second = _benches(tmp_path)
    first.create_queued(metadata(), resource_key="host:shop.example.com")

    with pytest.raises(TaskConflictError, match="Another bench's active task"):
        second.create_queued({**metadata(), "task_id": OTHER_ID}, resource_key="host:shop.example.com")


def test_the_idempotent_path_checks_other_benches_too(tmp_path: Path) -> None:
    first, second = _benches(tmp_path)
    first.create_queued(metadata(), resource_key="host:shop.example.com")

    with pytest.raises(TaskConflictError, match="Another bench's active task"):
        second.create_idempotent_queued(
            {**metadata(), "task_id": OTHER_ID},
            {},
            idempotency_digest="digest",
            request_fingerprint="fingerprint",
            resource_key=["site:shop.example.com", "host:shop.example.com"],
        )


def test_other_resources_stay_bench_local(tmp_path: Path) -> None:
    first, second = _benches(tmp_path)
    first.create_queued(metadata(), resource_key="site:shop.example.com")

    second.create_queued({**metadata(), "task_id": OTHER_ID}, resource_key="site:shop.example.com")

    assert second.read_metadata(OTHER_ID)["resource_keys"] == ["site:shop.example.com"]


def test_different_hostnames_do_not_conflict(tmp_path: Path) -> None:
    first, second = _benches(tmp_path)
    first.create_queued(metadata(), resource_key="host:one.example.com")

    second.create_queued({**metadata(), "task_id": OTHER_ID}, resource_key="host:two.example.com")


def test_a_finished_task_on_another_bench_releases_the_hostname(tmp_path: Path) -> None:
    first, second = _benches(tmp_path)
    first.create_queued(metadata(), resource_key="host:shop.example.com")
    first.transition(TASK_ID, TaskStatus.QUEUED, TaskStatus.RUNNING)
    first.transition(TASK_ID, TaskStatus.RUNNING, TaskStatus.SUCCESS)

    second.create_queued({**metadata(), "task_id": OTHER_ID}, resource_key="host:shop.example.com")
