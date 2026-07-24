import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task


@dataclass(kw_only=True)
class UpdateTask(Task):
    """Chain link: update/reinstall/rebuild apps, then queue the first site migration."""

    command: ClassVar[str] = "update"

    operation_id: str

    def run(self) -> None:
        operation = self.bench.migrations.get(self.operation_id)
        try:
            operation.update_apps(on_step=self.step, on_progress=self.report)
        except Exception:
            self.step_failed()
            sys.exit(1)
        operation.enqueue_next(handoff_from=operation.chain[-1]["task_id"])


if __name__ == "__main__":
    UpdateTask.main()
