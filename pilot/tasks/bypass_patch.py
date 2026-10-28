import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.exceptions import BenchError
from pilot.tasks import Task, on_cancel, on_failure, step


@dataclass(kw_only=True)
class BypassPatchTask(Task):
    command: ClassVar[str] = "bypass-patch"
    timeout_seconds: ClassVar[int] = 300

    operation_id: str
    patch: str

    def run(self) -> None:
        self.bypass()

    @step("bypass_patch", lambda self: f"Skip patch {self.patch}")
    def bypass(self) -> None:
        operation = self.bench.migrations.get(self.operation_id)
        if self.running_task_id:
            operation.task_ids["bypass_patch"] = self.running_task_id
        try:
            operation.bypass_patch(self.patch, on_progress=self.report)
        except BenchError as error:
            self.report(str(error))
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
    BypassPatchTask.main()
