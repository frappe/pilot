from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks.base import BaseTask, step


@dataclass(kw_only=True)
class SetupProductionTask(BaseTask):
    command: ClassVar[str] = "setup-production"

    def run(self) -> None:
        self.setup_production()

    @step("production", "Set up production")
    def setup_production(self) -> None:
        self.bench.setup_production(on_progress=self.report)


if __name__ == "__main__":
    SetupProductionTask.main()
