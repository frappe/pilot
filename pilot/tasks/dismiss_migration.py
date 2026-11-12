import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task


@dataclass(kw_only=True)
class DismissMigrationTask(Task):
    """Close a failed migration without recovering it, so updates are no longer blocked."""

    command: ClassVar[str] = "dismiss-migration"
    timeout_seconds: ClassVar[int] = 300

    operation_id: str

    def run(self) -> None:
        """Dismiss the migration and restore the sites."""
        operation = self.bench.migrations.get(self.operation_id)
        if self.running_task_id:
            operation.task_ids["dismiss"] = self.running_task_id
        try:
            operation.dismiss()
        except Exception:
            self.step_failed()
            sys.exit(1)


if __name__ == "__main__":
    DismissMigrationTask.main()
