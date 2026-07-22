import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.exceptions import MigrateError
from pilot.tasks import Task


@dataclass(kw_only=True)
class UpdateTask(Task):
    command: ClassVar[str] = "update"
    # Bench.update() emits its own on_step("done", ...) as its last phase.
    has_done_step: ClassVar[bool] = False

    apps: list[str] | None = None
    skip_failing_patches: bool = False

    def run(self) -> None:
        try:
            self.bench.update(
                apps_filter=set(self.apps) if self.apps else None,
                skip_failing_patches=self.skip_failing_patches,
                on_step=self._start_step,
                on_progress=self.report,
            )
        except MigrateError:
            self.step_failed()
            sys.exit(1)

    def _start_step(self, key: str, label: str) -> None:
        self.step(key, label)


if __name__ == "__main__":
    UpdateTask.main()
