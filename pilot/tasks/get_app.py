from dataclasses import dataclass
from typing import ClassVar

from pilot.core.app import App
from pilot.integrations.marketplace import Marketplace

from pilot.tasks.base import BaseTask, step


@dataclass(kw_only=True)
class GetAppTask(BaseTask):
    command: ClassVar[str] = "get-app"
    required_submit_args: ClassVar[tuple[str, ...]] = ("name",)

    repo: str = ""
    branch: str = ""
    marketplace_app: str = ""

    def run(self) -> None:
        self.fetch()

    @step("fetch", lambda self: f"Fetch {self.marketplace_app or self.repo}")
    def fetch(self) -> None:
        if self.marketplace_app:
            resolver = Marketplace(self.bench).find_app(self.marketplace_app)
            repo, branch = resolver.repo, resolver.target
        else:
            repo, branch = self.repo, self.branch
        app = App.from_repo(self.bench, repo, branch)
        app.install(install_dependencies=bool(self.marketplace_app), on_progress=self.report)


if __name__ == "__main__":
    GetAppTask.main()
