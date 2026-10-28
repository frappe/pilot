from dataclasses import dataclass, field
from typing import ClassVar

from pilot.config import AppConfig
from pilot.core.app import App
from pilot.core.app.install_result import AppInstallResult
from pilot.integrations.marketplace import Marketplace
from pilot.tasks import Task, step


@dataclass(kw_only=True)
class GetAndInstallAppTask(Task):
    """Fetch an app and optionally install it on sites."""

    command: ClassVar[str] = "get-and-install-app"
    is_cancellable_while_running: ClassVar[bool] = False

    repo: str = ""
    branch: str = ""
    marketplace_app: str = ""
    site: str = ""
    sites: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.site and not self.sites:
            self.sites = [self.site]

    def run(self) -> None:
        result = self.fetch()
        self.reload_for_new_app()
        self.install_on_sites(result.app)

    @step("reload", "Reload workers for the new app")
    def reload_for_new_app(self) -> None:
        """install-app enqueues jobs that import the app, and the running workers
        were started before it existed - they must restart before the site install."""
        self.bench.reload_workers(raises=True)

    @step("fetch", lambda self: f"Fetch {self.marketplace_app or self.repo}")
    def fetch(self) -> AppInstallResult:
        # Marketplace apps can bring dependency apps; raw repos do not.
        commit = ""
        if self.marketplace_app:
            resolver = Marketplace(self.bench).find_app(self.marketplace_app)
            commit = resolver.commit
            app = App(AppConfig(name=resolver.app, repo=resolver.repo, branch=resolver.branch), self.bench)
        else:
            app = App.from_repo(self.bench, self.repo, self.branch)
        return app.install(
            install_dependencies=bool(self.marketplace_app), commit=commit, on_progress=self.report
        )

    def install_on_sites(self, app: App) -> None:
        for site in self.sites:
            safe_key = site.replace(".", "_").replace("-", "_")
            with self.step(f"install_{safe_key}_{app.config.name}", f"Install {app.config.name} on {site}"):
                self.bench.site(site).install_app(app)


if __name__ == "__main__":
    GetAndInstallAppTask.main()
