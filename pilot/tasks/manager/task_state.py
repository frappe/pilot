from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    KILLED = "killed"


TERMINAL_TASK_STATUSES = frozenset({TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.KILLED})
ACTIVE_TASK_STATUSES = frozenset({TaskStatus.QUEUED, TaskStatus.RUNNING})

ALLOWED_TASK_TRANSITIONS = {
    TaskStatus.QUEUED: frozenset({TaskStatus.RUNNING, TaskStatus.KILLED}),
    TaskStatus.RUNNING: TERMINAL_TASK_STATUSES,
    TaskStatus.SUCCESS: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.KILLED: frozenset(),
}


def parse_task_status(value: str) -> TaskStatus:
    try:
        return TaskStatus(value)
    except ValueError as error:
        raise ValueError(f"Unknown task status: {value!r}") from error


def validate_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    if target not in ALLOWED_TASK_TRANSITIONS[current]:
        raise ValueError(f"Invalid task transition: {current.value} -> {target.value}")
