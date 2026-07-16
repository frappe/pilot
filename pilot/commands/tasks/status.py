from __future__ import annotations

from pilot.commands.base import Command


class TaskWorkerStatusCommand(Command):
    name = "status"
    help = "Show the Admin task worker state."
    group = "tasks"

    def run(self) -> None:
        from pilot.tasks.manager.activity import TaskActivityReader

        activity = TaskActivityReader(self.bench.path).read()
        print(
            f"Task worker: {activity.worker_status} "
            f"(desired: {activity.desired_status})"
        )
        state = activity.worker_state
        if state is not None and state.current_task_id:
            print(f"Current task: {state.current_task_id}")
        print(
            f"Task activity: {'active' if activity.active else 'idle'} "
            f"(queued: {activity.queued_tasks}, running: {activity.running_tasks})"
        )
