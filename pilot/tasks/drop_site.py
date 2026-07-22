from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class DropSiteTask(Task):
    command: ClassVar[str] = "drop-site"

    site: str

    def run(self) -> None:
        self.require_production_privileges()
        self.drop()

    @step("drop", lambda self: f"Drop site {self.site}")
    def drop(self) -> None:
        self.bench.site(self.site).drop(on_progress=self.report)


if __name__ == "__main__":
    DropSiteTask.main()
