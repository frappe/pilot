from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands.base import Command


@dataclass(kw_only=True)
class TaskWorkerStatusCommand(Command):
    name: ClassVar[str] = "status"
    help: ClassVar[str] = "Show the Admin task worker state."
    group: ClassVar[str] = "tasks"

    def run(self) -> None:
        from pilot.tasks.manager.activity import TaskActivityReader

        activity = TaskActivityReader(self.bench.path).read()
        self.print(
            f"Task worker: {activity.worker_status} "
            f"(desired: {activity.desired_status})"
        )
        state = activity.worker_state
        if state is not None and state.current_task_id:
            self.print(f"Current task: {state.current_task_id}")
        self.print(
            f"Task activity: {'active' if activity.active else 'idle'} "
            f"(queued: {activity.queued_tasks}, running: {activity.running_tasks})"
        )
