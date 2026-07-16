from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pilot.tasks.manager.task_state import TaskStatus, parse_task_status
from pilot.tasks.manager.worker_state import (
    WorkerIntent,
    WorkerState,
    WorkerStatus,
    WorkerStore,
)

_TASK_ID_PATTERN = re.compile(r"^\d{8}-\d{6}-[a-f0-9]{6}$")
_ACTIVE_WORKER_STATUSES = frozenset(
    {WorkerStatus.STARTING, WorkerStatus.RUNNING, WorkerStatus.DRAINING}
)


@dataclass(frozen=True)
class TaskActivity:
    active: bool
    uncertain: bool
    worker_state: WorkerState | None
    worker_intent: WorkerIntent | None
    queued_tasks: int
    running_tasks: int

    @property
    def worker_status(self) -> str:
        if self.worker_state is not None:
            return self.worker_state.status.value
        return "unknown" if self.uncertain else "not-started"

    @property
    def desired_status(self) -> str:
        return self.worker_intent.value if self.worker_intent is not None else "unknown"

    def public_dict(self) -> dict[str, bool | str]:
        return {
            "active": self.active,
            "uncertain": self.uncertain,
            "status": self.worker_status,
            "desired": self.desired_status,
        }


class TaskActivityReader:
    def __init__(self, bench_root: Path) -> None:
        self._tasks_root = Path(bench_root) / "tasks"
        self._worker = WorkerStore(bench_root)

    def read(self) -> TaskActivity:
        worker_state, state_uncertain = self._read_worker_state()
        worker_intent, intent_uncertain = self._read_worker_intent()
        queued, running, tasks_uncertain = self._read_task_counts()
        uncertain = state_uncertain or intent_uncertain or tasks_uncertain
        worker_active = (
            worker_state is not None
            and worker_state.status in _ACTIVE_WORKER_STATUSES
        )
        return TaskActivity(
            active=uncertain or worker_active or running > 0,
            uncertain=uncertain,
            worker_state=worker_state,
            worker_intent=worker_intent,
            queued_tasks=queued,
            running_tasks=running,
        )

    def _read_worker_state(self) -> tuple[WorkerState | None, bool]:
        try:
            return self._worker.read_state(), False
        except (KeyError, OSError, TypeError, ValueError):
            return None, True

    def _read_worker_intent(self) -> tuple[WorkerIntent | None, bool]:
        try:
            return self._worker.read_intent(), False
        except (KeyError, OSError, TypeError, ValueError):
            return None, True

    def _read_task_counts(self) -> tuple[int, int, bool]:
        if not self._tasks_root.exists():
            return 0, 0, False
        queued = 0
        running = 0
        uncertain = False
        try:
            entries = list(self._tasks_root.iterdir())
        except OSError:
            return 0, 0, True
        for task_dir in entries:
            if task_dir.is_symlink() or not task_dir.is_dir():
                continue
            if not _TASK_ID_PATTERN.match(task_dir.name):
                continue
            try:
                status = parse_task_status(
                    (task_dir / "status").read_text(encoding="utf-8").strip()
                )
            except (OSError, ValueError):
                uncertain = True
                continue
            queued += status == TaskStatus.QUEUED
            running += status == TaskStatus.RUNNING
        return queued, running, uncertain
