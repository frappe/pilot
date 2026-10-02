from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class CloneBenchTask(Task):
    command: ClassVar[str] = "clone-bench"
    is_cancellable_while_running: ClassVar[bool] = False
    source_bench: str
    name: str

    @step("clone", lambda self: f"Clone {self.source_bench} into {self.name}")
    def run(self) -> None:
        from pilot.core.bench import Bench

        Bench(self.bench.path.parent / self.source_bench).clone(self.name, self.report)


if __name__ == "__main__":
    CloneBenchTask.main()
