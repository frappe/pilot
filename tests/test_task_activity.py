from __future__ import annotations

from pathlib import Path

import pytest

from pilot.tasks.manager.activity import TaskActivityReader
from pilot.tasks.manager.task_state import TaskStatus
from pilot.tasks.manager.task_store import TaskStore
from pilot.tasks.manager.worker_state import (
    WorkerIntent,
    WorkerStatus,
    WorkerStore,
)

TASK_ID = "20260715-120000-aabbcc"


def create_task(bench_root: Path, status: TaskStatus) -> None:
    store = TaskStore(bench_root)
    store.create_queued(
        {
            "task_id": TASK_ID,
            "command": "build",
            "args": {},
            "command_argv": ["bench", "build"],
            "queued_at": "2026-07-15T12:00:00+00:00",
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "failure": None,
            "bench_root": str(bench_root),
        }
    )
    if status == TaskStatus.RUNNING:
        store.transition(TASK_ID, TaskStatus.QUEUED, TaskStatus.RUNNING)


@pytest.mark.parametrize(
    "status",
    [WorkerStatus.STARTING, WorkerStatus.RUNNING, WorkerStatus.DRAINING],
)
def test_active_worker_states_report_activity(
    tmp_path: Path,
    status: WorkerStatus,
) -> None:
    WorkerStore(tmp_path).write_state(status, 4321)

    activity = TaskActivityReader(tmp_path).read()

    assert activity.active is True
    assert activity.uncertain is False
    assert activity.worker_status == status.value


def test_running_task_reports_activity_even_when_worker_is_stopped(tmp_path: Path) -> None:
    create_task(tmp_path, TaskStatus.RUNNING)
    WorkerStore(tmp_path).write_state(WorkerStatus.STOPPED, None)

    activity = TaskActivityReader(tmp_path).read()

    assert activity.active is True
    assert activity.running_tasks == 1


def test_queued_only_work_does_not_block_idle_shutdown(tmp_path: Path) -> None:
    create_task(tmp_path, TaskStatus.QUEUED)
    worker = WorkerStore(tmp_path)
    worker.write_state(WorkerStatus.STOPPED, None)
    worker.write_intent(WorkerIntent.STOPPED)

    activity = TaskActivityReader(tmp_path).read()

    assert activity.active is False
    assert activity.queued_tasks == 1
    assert activity.desired_status == "stopped"


@pytest.mark.parametrize("filename", ["worker-state.json", f"{TASK_ID}/status"])
def test_corrupt_activity_state_fails_safe(tmp_path: Path, filename: str) -> None:
    create_task(tmp_path, TaskStatus.QUEUED)
    WorkerStore(tmp_path).write_state(WorkerStatus.IDLE, 4321)
    (tmp_path / "tasks" / filename).write_text("not-valid")

    activity = TaskActivityReader(tmp_path).read()

    assert activity.active is True
    assert activity.uncertain is True
