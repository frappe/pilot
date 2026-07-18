from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks.base import BaseTask, step


@dataclass(kw_only=True)
class RemoveAppTask(BaseTask):
    command: ClassVar[str] = "remove-app"

    name: str

    def run(self) -> None:
        self.remove()

    @step("remove", lambda self: f"Remove {self.name}")
    def remove(self) -> None:
        self.bench.app(self.name).remove(force=True, on_progress=self.report)


if __name__ == "__main__":
    RemoveAppTask.main()
