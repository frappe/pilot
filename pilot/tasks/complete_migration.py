from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, on_cancel, on_failure, step


@dataclass(kw_only=True)
class CompleteMigrationTask(Task):
    command: ClassVar[str] = "complete-migration"

    operation_id: str

    @step("restart", "Restart services and finish migration")
    def run(self) -> None:
        self.bench.migrations.get_for_chain_task(
            self.operation_id, self.running_task_id, self.command, {}
        ).complete()

    @on_failure
    @on_cancel
    def fail_migration_step(self) -> dict:
        return {"operation_id": self.operation_id, "phase": "migrating", "site": None}


if __name__ == "__main__":
    CompleteMigrationTask.main()
