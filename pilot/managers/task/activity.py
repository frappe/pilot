from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pilot.internal.tasks.files import TaskFiles
from pilot.internal.tasks.state import parse_task_status
from pilot.internal.tasks.worker_state import (
    WorkerIntent,
    WorkerState,
    WorkerStatus,
    WorkerStore,
)
from pilot.managers.task.models import TaskStatus


@dataclass(frozen=True)
class TaskActivity:
    active: bool
    uncertain: bool
    worker_status: str
    desired_status: str
    current_task_id: str | None
    queued_tasks: int
    running_tasks: int

    @property
    def public_dict(self) -> dict[str, bool | str]:
        """The activity for the API."""
        return {
            "active": self.active,
            "uncertain": self.uncertain,
            "status": self.worker_status,
            "desired": self.desired_status,
        }


class TaskActivityReader:
    active_worker_statuses = frozenset({WorkerStatus.STARTING, WorkerStatus.RUNNING, WorkerStatus.DRAINING})

    def __init__(self, bench_root: Path) -> None:
        self._files = TaskFiles(Path(bench_root) / "tasks")
        self._worker = WorkerStore(bench_root)

    def read(self) -> TaskActivity:
        """The worker state and task counts."""
        worker_state, state_uncertain = self._read_worker_state()
        worker_intent, intent_uncertain = self._read_worker_intent()
        queued, running, tasks_uncertain = self._read_task_counts()
        uncertain = state_uncertain or intent_uncertain or tasks_uncertain
        worker_active = worker_state is not None and worker_state.status in self.active_worker_statuses
        # Queued work holds the admin open until the worker claims it.
        pending = queued > 0 and worker_intent != WorkerIntent.STOPPED
        return TaskActivity(
            active=uncertain or worker_active or running > 0 or pending,
            uncertain=uncertain,
            worker_status=self._worker_status(worker_state, uncertain),
            desired_status=self._desired_status(worker_intent),
            current_task_id=worker_state.current_task_id if worker_state else None,
            queued_tasks=queued,
            running_tasks=running,
        )

    def _read_worker_state(self) -> tuple[WorkerState | None, bool]:
        """The worker state, and whether it could not be read."""
        try:
            return self._worker.read_state(), False
        except (KeyError, OSError, TypeError, ValueError):
            return None, True

    def _read_worker_intent(self) -> tuple[WorkerIntent | None, bool]:
        """The worker intent, and whether it could not be read."""
        try:
            return self._worker.read_intent(), False
        except (KeyError, OSError, TypeError, ValueError):
            return None, True

    @staticmethod
    def _worker_status(worker_state: WorkerState | None, uncertain: bool) -> str:
        """The worker status for the API."""
        if worker_state is not None:
            return worker_state.status.value
        return "unknown" if uncertain else "not-started"

    @staticmethod
    def _desired_status(worker_intent: WorkerIntent | None) -> str:
        """The desired worker status for the API."""
        return worker_intent.value if worker_intent is not None else "unknown"

    def _read_task_counts(self) -> tuple[int, int, bool]:
        """The queued and running counts, and whether one could not be read."""
        queued = 0
        running = 0
        uncertain = False
        try:
            entries = list(self._files.task_dirs())
        except OSError:
            return 0, 0, True
        for task_dir in entries:
            try:
                status = parse_task_status((task_dir / "status").read_text(encoding="utf-8").strip())
            except (OSError, ValueError):
                uncertain = True
                continue
            queued += status == TaskStatus.QUEUED
            running += status == TaskStatus.RUNNING
        return queued, running, uncertain
