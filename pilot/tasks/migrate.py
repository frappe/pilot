import subprocess
import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class MigrateTask(Task):
    command: ClassVar[str] = "migrate"

    site: str

    def run(self) -> None:
        self.migrate()

    @step("migrate", lambda self: f"Migrate site {self.site}")
    def migrate(self) -> None:
        result = subprocess.run([*self.bench.frappe_call, "frappe", "--site", self.site, "migrate"])
        if result.returncode != 0:
            sys.exit(result.returncode)


if __name__ == "__main__":
    MigrateTask.main()
