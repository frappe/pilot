from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class SetupNginxTask(Task):
    command: ClassVar[str] = "setup-nginx"

    def run(self) -> None:
        self.setup_nginx()

    @step("nginx", "Set up Nginx")
    def setup_nginx(self) -> None:
        self.bench.setup_nginx(on_progress=self.report)


if __name__ == "__main__":
    SetupNginxTask.main()
