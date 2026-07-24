import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task


@dataclass(kw_only=True)
class RetryUpdateTask(Task):
    """Re-arm a failed migration and resume its chain from the failed unit."""

    command: ClassVar[str] = "retry-update"

    operation_id: str

    def run(self) -> None:
        operation = self.bench.migrations.get(self.operation_id)
        try:
            operation.retry_arm()
        except Exception:
            self.step_failed()
            sys.exit(1)
        operation.enqueue_next(handoff_from=operation.task_ids.get("retry"))


if __name__ == "__main__":
    RetryUpdateTask.main()
