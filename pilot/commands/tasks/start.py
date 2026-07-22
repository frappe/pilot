from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import Command


@dataclass(kw_only=True)
class StartTaskWorkerCommand(Command):
    name: ClassVar[str] = "start"
    help: ClassVar[str] = "Allow the Admin task worker to run queued tasks."
    group: ClassVar[str] = "tasks"

    def run(self) -> None:
        from pilot.managers.task import TaskWorkerControl

        TaskWorkerControl(self.bench.path).request_start()
        self.report("Task worker start requested.")
