from dataclasses import dataclass, field
from typing import ClassVar

from pilot.config.app import AppConfig
from pilot.core.app import App
from pilot.core.site import Site
from pilot.integrations.marketplace import Marketplace

from pilot.tasks.base import BaseTask, step


@dataclass(kw_only=True)
class GetAndInstallAppTask(BaseTask):
    """Fetch an app (by repo or marketplace name) and install it on zero or
    more sites. Zero sites is valid: it just fetches (and, for marketplace
    apps, resolves dependencies) without installing anywhere."""

    command: ClassVar[str] = "get-and-install-app"

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
        # Frappe's site install-app cascades installing declared dependencies
        # onto the site itself, but never builds their assets — that's a
        # separate, bench-wide step this task still owns.
        self.install_on_sites(result.app)
        self.build_assets([result.app] + result.installed_dependencies)

    @step("fetch", lambda self: f"Fetch {self.marketplace_app or self.repo}")
    def fetch(self):
        # get-app resolves and installs marketplace dependencies itself; a
        # plain repo has none to resolve.
        if self.marketplace_app:
            resolver = Marketplace(self.bench).find_app(self.marketplace_app)
            app = App(
                AppConfig(name=resolver.app, repo=resolver.repo, branch=resolver.target), self.bench
            )
        else:
            app = App.from_repo(self.bench, self.repo, self.branch)
        return app.install(
            install_dependencies=bool(self.marketplace_app), on_progress=self.report
        )

    def install_on_sites(self, app: App) -> None:
        for site in self.sites:
            safe_key = site.replace(".", "_").replace("-", "_")
            with self.step(
                f"install_{safe_key}_{app.config.name}", f"Install {app.config.name} on {site}"
            ):
                Site.for_name(site, self.bench).install_app(app)

    def build_assets(self, apps: list[App]) -> None:
        from pilot.managers.python_environment import PythonEnvManager

        env = PythonEnvManager(self.bench)
        for app in apps:
            with self.step(f"build_{app.config.name}", f"Build assets for {app.config.name}"):
                env.build_assets_for_app(app)


if __name__ == "__main__":
    GetAndInstallAppTask.main()
