from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

__all__ = ["TaskFailure", "TaskInfo", "TaskStatus"]


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


@dataclass
class TaskInfo:
    task_id: str
    command: str
    args: dict
    status: TaskStatus
    pid: int | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    exit_code: int | None
    output_path: Path
    queue_position: int | None = None
    failure: TaskFailure | None = None

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "command": self.command,
            "args": self.args,
            "status": self.status,
            "pid": self.pid,
            "queued_at": self.queued_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "exit_code": self.exit_code,
            "duration_seconds": self.duration_seconds,
            "queue_position": self.queue_position,
            "failure": (
                {"code": self.failure.code, "message": self.failure.message}
                if self.failure
                else None
            ),
        }
