from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step
from pilot.utils import cli_root


@dataclass(kw_only=True)
class UpdateCliTask(Task):
    command: ClassVar[str] = "update-cli"

    def run(self) -> None:
        self.update()

    @step("update", lambda self: f"Update bench-cli at {cli_root()}")
    def update(self) -> None:
        subprocess.run(["git", "-C", str(cli_root()), "pull"], check=True)


if __name__ == "__main__":
    UpdateCliTask.main()
