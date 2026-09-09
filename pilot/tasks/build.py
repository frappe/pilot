from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class BuildTask(Task):
    command: ClassVar[str] = "build"

    app: str | None = None

    def run(self) -> None:
        self.build()

    @step("build", lambda self: f"Build assets for {self.app}" if self.app else "Build assets")
    def build(self) -> None:
        self.bench.rebuild_assets(apps=[self.app] if self.app else None)


if __name__ == "__main__":
    BuildTask.main()
