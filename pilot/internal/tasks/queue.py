from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pilot.internal.tasks.files import TaskFiles
from pilot.internal.tasks.models import TaskStatus
from pilot.internal.tasks.store import TaskStore


class TaskQueue:
    def __init__(self, bench_root: Path) -> None:
        self._store = TaskStore(bench_root)
        self._files = TaskFiles(self._store.tasks_root)

    def queued_task_ids(self) -> list[str]:
        with self._store.locked():
            return [task_id for _, task_id in self._queued_tasks_locked()]

    def positions(self) -> dict[str, int]:
        return {task_id: position for position, task_id in enumerate(self.queued_task_ids(), start=1)}

    def claim_next(self) -> str | None:
        with self._store.locked():
            queued = self._queued_tasks_locked()
            if not queued:
                return None
            task_id = queued[0][1]
            started_at = datetime.now(UTC).isoformat()
            claimed = self._store.transition_locked(
                task_id,
                TaskStatus.QUEUED,
                TaskStatus.RUNNING,
                {"started_at": started_at},
            )
            return task_id if claimed else None

    def _queued_tasks_locked(self) -> list[tuple[tuple, str]]:
        queued = []
        for task_dir in self._files.task_dirs():
            task_id = task_dir.name
            try:
                if self._store.read_status(task_id) != TaskStatus.QUEUED:
                    continue
                metadata = self._store.read_metadata(task_id)
                queued.append((self._fifo_key(metadata, task_id), task_id))
            except (OSError, ValueError):
                continue
        queued.sort()
        return queued

    @staticmethod
    def _fifo_key(metadata: dict, task_id: str) -> tuple:
        sequence = metadata.get("queue_sequence")
        if isinstance(sequence, int):
            return (1, sequence, task_id)
        queued_at = metadata.get("queued_at") or metadata.get("started_at") or ""
        return (0, queued_at, task_id)
