from __future__ import annotations

from pilot.managers.task.models import TaskFailure, TaskStatus

_TERMINAL_TASK_STATUSES = frozenset(
    {TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.KILLED}
)

_ALLOWED_TASK_TRANSITIONS = {
    TaskStatus.QUEUED: frozenset({TaskStatus.RUNNING, TaskStatus.KILLED}),
    TaskStatus.RUNNING: _TERMINAL_TASK_STATUSES,
    TaskStatus.SUCCESS: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.KILLED: frozenset(),
}

_FAILURE_MESSAGES = {
    "command_failed": "Task command failed.",
    "task_interrupted": "Task execution was interrupted.",
}


def parse_task_status(value: str) -> TaskStatus:
    try:
        return TaskStatus(value)
    except ValueError as error:
        raise ValueError(f"Unknown task status: {value!r}") from error


def validate_task_transition(current: TaskStatus, target: TaskStatus) -> None:
    if target not in _ALLOWED_TASK_TRANSITIONS[current]:
        raise ValueError(f"Invalid task transition: {current.value} -> {target.value}")


def safe_task_failure(value: object, status: TaskStatus) -> TaskFailure | None:
    if status != TaskStatus.FAILED:
        return None
    code = value.get("code") if isinstance(value, dict) else None
    if code not in _FAILURE_MESSAGES:
        code = "command_failed"
    return TaskFailure(code=code, message=_FAILURE_MESSAGES[code])
