from __future__ import annotations

from pilot.commands.base import Command


class StartTaskWorkerCommand(Command):
    name = "start"
    help = "Allow the Admin task worker to run queued tasks."
    group = "tasks"

    def run(self) -> None:
        from pilot.tasks.manager.worker_state import WorkerIntent, WorkerStore

        WorkerStore(self.bench.path).write_intent(WorkerIntent.RUNNING)
        print("Task worker start requested.")
