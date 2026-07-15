from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from admin.backend.tasks.manager.task_state import TaskStatus


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

    @property
    def duration_seconds(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()
