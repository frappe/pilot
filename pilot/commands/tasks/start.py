from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands.base import Command


@dataclass(kw_only=True)
class StartTaskWorkerCommand(Command):
    name: ClassVar[str] = "start"
    help: ClassVar[str] = "Allow the Admin task worker to run queued tasks."
    group: ClassVar[str] = "tasks"

    def run(self) -> None:
        from pilot.tasks.manager.worker_state import WorkerIntent, WorkerStore

        WorkerStore(self.bench.path).write_intent(WorkerIntent.RUNNING)
        self.print("Task worker start requested.")
