import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.exceptions import MigrateError
from pilot.tasks import Task


@dataclass(kw_only=True)
class UpdateTask(Task):
    command: ClassVar[str] = "update"

    operation_id: str | None = None
    apps: list[str] | None = None
    skip_failing_patches: bool = False

    def run(self) -> None:
        operation = self._operation()
        try:
            operation.execute_update(on_step=self._start_step, on_progress=self.report)
        except MigrateError:
            self.step_failed()
            sys.exit(1)
        if not operation.is_resolved:
            self.step_failed()
            sys.exit(1)

    def _operation(self):
        if self.operation_id:
            return self.bench.migrations.get(self.operation_id)
        return self.bench.migrations.create_update(
            set(self.apps) if self.apps else None,
            self.skip_failing_patches,
        )

    def _start_step(self, key: str, label: str) -> None:
        self.step(key, label)


if __name__ == "__main__":
    UpdateTask.main()
