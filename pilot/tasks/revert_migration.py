import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task


@dataclass(kw_only=True)
class RevertMigrationTask(Task):
    """Re-arm a failed migration for restore and queue the first revert-chain task."""

    command: ClassVar[str] = "revert-migration"

    operation_id: str

    def run(self) -> None:
        operation = self.bench.migrations.get(self.operation_id)
        try:
            operation.revert_arm()
        except Exception:
            self.step_failed()
            sys.exit(1)
        operation.enqueue_next(handoff_from=operation.task_ids.get("restore"))


if __name__ == "__main__":
    RevertMigrationTask.main()
