from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class ForkBenchTask(Task):
    command: ClassVar[str] = "fork-bench"
    is_cancellable_while_running: ClassVar[bool] = False

    target: str
    site_template: str

    @step("fork", lambda self: f"Fork bench into {self.target}")
    def run(self) -> None:
        destination = self.bench.fork(self.target, Path(self.site_template), self.report)
        self.report(f"Ready: {destination.path}")


if __name__ == "__main__":
    ForkBenchTask.main()
