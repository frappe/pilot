from dataclasses import dataclass, field
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class CloneBenchTask(Task):
    command: ClassVar[str] = "clone-bench"
    is_cancellable_while_running: ClassVar[bool] = False
    source_bench: str
    name: str
    branch: str = "default"
    app_branches: dict[str, str] = field(default_factory=dict)

    @step("clone", lambda self: f"Clone {self.source_bench} into {self.name}")
    def run(self) -> None:
        from pilot.core.bench import Bench

        Bench(self.bench.path.parent / self.source_bench).clone(
            self.name, self.report, branch=self.branch, app_branches=self.app_branches
        )


if __name__ == "__main__":
    CloneBenchTask.main()
