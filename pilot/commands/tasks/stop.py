from __future__ import annotations

from pilot.commands.base import Command


class StopTaskWorkerCommand(Command):
    name = "stop"
    help = "Drain the Admin task worker and leave queued tasks waiting."
    group = "tasks"

    def run(self) -> None:
        from pilot.tasks.manager.worker_state import WorkerIntent, WorkerStore

        WorkerStore(self.bench.path).write_intent(WorkerIntent.STOPPED)
        print("Task worker will stop after its current task.")
