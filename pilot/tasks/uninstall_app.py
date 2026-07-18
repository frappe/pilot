from dataclasses import dataclass
from typing import ClassVar

from pilot.core.site import Site
from pilot.tasks.base import BaseTask, step


@dataclass(kw_only=True)
class UninstallAppTask(BaseTask):
    command: ClassVar[str] = "uninstall-app"

    site: str
    app: str
    force: bool = False

    def run(self) -> None:
        self.uninstall()

    @step("uninstall", lambda self: f"Uninstall {self.app} from {self.site}")
    def uninstall(self) -> None:
        site = Site.for_name(self.site, self.bench)
        site.uninstall_apps([self.app], force=self.force, on_progress=self.report)


if __name__ == "__main__":
    UninstallAppTask.main()
