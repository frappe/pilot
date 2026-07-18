import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.exceptions import MigrateError
from pilot.tasks.base import BaseTask


@dataclass(kw_only=True)
class UpdateTask(BaseTask):
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
                on_step=self.step,
                on_progress=self.report,
            )
        except MigrateError:
            self.step_failed()
            sys.exit(1)


if __name__ == "__main__":
    UpdateTask.main()
