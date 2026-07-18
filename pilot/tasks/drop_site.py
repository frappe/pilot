from dataclasses import dataclass
from typing import ClassVar

from pilot.core.site import Site
from pilot.tasks.base import BaseTask, step


@dataclass(kw_only=True)
class DropSiteTask(BaseTask):
    command: ClassVar[str] = "drop-site"

    site: str

    def run(self) -> None:
        self.require_production_privileges()
        self.drop()

    @step("drop", lambda self: f"Drop site {self.site}")
    def drop(self) -> None:
        Site.for_name(self.site, self.bench).drop(on_progress=self.report)


if __name__ == "__main__":
    DropSiteTask.main()
