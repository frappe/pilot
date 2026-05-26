from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class TaskInfo:
    task_id: str
    command: str
    args: dict
    status: str  # 'running' | 'success' | 'failed' | 'killed'
    pid: int | None
    started_at: datetime
    finished_at: datetime | None
    exit_code: int | None
    output_path: Path

    @property
    def duration_seconds(self) -> float | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()
