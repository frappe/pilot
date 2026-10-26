from dataclasses import dataclass
from typing import ClassVar

from pilot.commands import Command


@dataclass(kw_only=True)
class WaitForDatabaseCommand(Command):
    name: ClassVar[str] = "wait-for-database"
    help: ClassVar[str] = "Wait for the managed database before starting the workload."

    def run(self) -> None:
        from pilot.core.bench.runtime import BenchRuntime

        BenchRuntime(self.bench).wait_for_database()
