from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import Command


@dataclass(kw_only=True)
class TaskWorkerStatusCommand(Command):
    name: ClassVar[str] = "status"
    help: ClassVar[str] = "Show the Admin task worker state."
    group: ClassVar[str] = "tasks"

    def run(self) -> None:
        from pilot.managers.task import TaskActivityReader

        activity = TaskActivityReader(self.bench.path).read()
        self.report(f"Task worker: {activity.worker_status} (desired: {activity.desired_status})")
        if activity.current_task_id:
            self.report(f"Current task: {activity.current_task_id}")
        self.report(
            f"Task activity: {'active' if activity.active else 'idle'} "
            f"(queued: {activity.queued_tasks}, running: {activity.running_tasks})"
        )
