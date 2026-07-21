import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task


@dataclass(kw_only=True)
class RevertMigrationTask(Task):
    command: ClassVar[str] = "revert-migration"

    operation_id: str

    def run(self) -> None:
        operation = self.bench.migrations.get(self.operation_id)
        try:
            operation.revert(on_step=self._start_step, on_progress=self.report)
        except Exception:
            self.step_failed()
            sys.exit(1)

    def _start_step(self, key: str, label: str) -> None:
        self.step(key, label)


if __name__ == "__main__":
    RevertMigrationTask.main()
