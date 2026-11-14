from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["TERMINAL_TASK_STATUSES", "TaskFailure", "TaskStatus"]


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    KILLED = "killed"

    @property
    def is_active(self) -> bool:
        """Whether a task with this status is queued or running."""
        return self in frozenset({TaskStatus.QUEUED, TaskStatus.RUNNING})

    @property
    def is_terminal(self) -> bool:
        """Whether a task with this status has finished."""
        return self in TERMINAL_TASK_STATUSES


TERMINAL_TASK_STATUSES = frozenset({TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.KILLED})


@dataclass(frozen=True)
class TaskFailure:
    code: str
    message: str
