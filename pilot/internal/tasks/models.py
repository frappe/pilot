from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["TaskFailure", "TaskStatus"]


class TaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    KILLED = "killed"

    @property
    def is_active(self) -> bool:
        return self in _ACTIVE_TASK_STATUSES

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_TASK_STATUSES


_TERMINAL_TASK_STATUSES = frozenset({TaskStatus.SUCCESS, TaskStatus.FAILED, TaskStatus.KILLED})
_ACTIVE_TASK_STATUSES = frozenset({TaskStatus.QUEUED, TaskStatus.RUNNING})


@dataclass(frozen=True)
class TaskFailure:
    code: str
    message: str
