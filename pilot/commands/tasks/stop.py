from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands.base import Command


@dataclass(kw_only=True)
class StopTaskWorkerCommand(Command):
    name: ClassVar[str] = "stop"
    help: ClassVar[str] = "Drain the Admin task worker and leave queued tasks waiting."
    group: ClassVar[str] = "tasks"

    def run(self) -> None:
        from pilot.tasks.manager.worker_state import WorkerIntent, WorkerStore

        WorkerStore(self.bench.path).write_intent(WorkerIntent.STOPPED)
        self.print("Task worker will stop after its current task.")
