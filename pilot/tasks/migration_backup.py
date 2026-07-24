import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task


@dataclass(kw_only=True)
class MigrationBackupTask(Task):
    """Chain link: back up one site's tables before a migration, then queue the next step."""

    command: ClassVar[str] = "migration-backup"

    operation_id: str
    site: str

    def run(self) -> None:
        operation = self.bench.migrations.get(self.operation_id)
        try:
            operation.back_up_site(self.site, on_step=self.step, on_progress=self.report)
        except Exception:
            self.step_failed()
            sys.exit(1)
        operation.enqueue_next(handoff_from=operation.chain[-1]["task_id"])


if __name__ == "__main__":
    MigrationBackupTask.main()

