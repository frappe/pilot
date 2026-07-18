import subprocess
import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks.base import BaseTask, step


@dataclass(kw_only=True)
class BuildTask(BaseTask):
    command: ClassVar[str] = "build"

    app: str | None = None

    def run(self) -> None:
        self.build()

    @step("build", lambda self: f"Build assets for {self.app}" if self.app else "Build assets")
    def build(self) -> None:
        argv = [*self.bench.frappe_call, "frappe", "build"]
        if self.app:
            argv += ["--app", self.app]
        result = subprocess.run(argv)
        if result.returncode != 0:
            sys.exit(result.returncode)


if __name__ == "__main__":
    BuildTask.main()
