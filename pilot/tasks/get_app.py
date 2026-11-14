from dataclasses import dataclass
from typing import ClassVar

from pilot.core.app import App
from pilot.integrations.marketplace import Marketplace
from pilot.tasks import Task, on_success, step


@dataclass(kw_only=True)
class GetAppTask(Task):
    command: ClassVar[str] = "get-app"
    required_submit_args: ClassVar[tuple[str, ...]] = ("name",)

    repo: str = ""
    branch: str = ""
    marketplace_app: str = ""

    def run(self) -> None:
        """Get the app."""
        self.fetch()

    @on_success
    def reload_workers(self) -> dict:
        """Long-lived web and background workers hold the old app list and
        import map, so they need a restart once this task lands."""
        return {"web_only": False}

    @step("fetch", lambda self: f"Fetch {self.marketplace_app or self.repo}")
    def fetch(self) -> None:
        """Get the app from the marketplace or a repository."""
        commit = ""
        if self.marketplace_app:
            resolver = Marketplace(self.bench).find_app(self.marketplace_app)
            repo, branch, commit = resolver.repo, resolver.branch, resolver.commit
        else:
            repo, branch = self.repo, self.branch
        app = App.from_repo(self.bench, repo, branch)
        app.install(
            install_dependencies=bool(self.marketplace_app), commit=commit, on_progress=self.report
        )


if __name__ == "__main__":
    GetAppTask.main()
