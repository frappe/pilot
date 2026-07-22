from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class UninstallAppTask(Task):
    command: ClassVar[str] = "uninstall-app"

    site: str
    app: str
    force: bool = False

    def run(self) -> None:
        self.uninstall()

    @step("uninstall", lambda self: f"Uninstall {self.app} from {self.site}")
    def uninstall(self) -> None:
        site = self.bench.site(self.site)
        site.uninstall_apps([self.app], force=self.force, on_progress=self.report)


if __name__ == "__main__":
    UninstallAppTask.main()
