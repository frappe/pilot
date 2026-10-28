import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, on_cancel, on_failure


@dataclass(kw_only=True)
class RetryUpdateTask(Task):
    """Re-arm a failed migration and resume its chain from the failed unit."""

    command: ClassVar[str] = "retry-update"
    timeout_seconds: ClassVar[int] = 300

    operation_id: str

    def run(self) -> None:
        operation = self.bench.migrations.get(self.operation_id)
        if self.running_task_id:
            operation.task_ids["retry"] = self.running_task_id
        try:
            operation.retry()
        except Exception:
            self.step_failed()
            sys.exit(1)
        operation.enqueue_next(handoff_from=self.running_task_id or None)

    @on_failure
    @on_cancel
    def fail_migration_step(self) -> dict | None:
        operation = self.bench.migrations.get(self.operation_id)
        if not operation.chain:
            return None
        return {
            "operation_id": self.operation_id,
            "phase": str(operation.state),
            "site": operation.failed_site,
            "previous_task_id": operation.chain[-1]["task_id"],
        }


if __name__ == "__main__":
    RetryUpdateTask.main()
