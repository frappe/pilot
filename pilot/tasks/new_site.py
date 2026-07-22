from dataclasses import dataclass, field
from typing import Annotated, ClassVar

from pilot.core.app import App
from pilot.core.site import Site
from pilot.integrations.marketplace import Marketplace
from pilot.tasks import Arg, Task, on_cancel, on_failure, step


@dataclass(kw_only=True)
class NewSiteTask(Task):
    command: ClassVar[str] = "new-site"

    name: str
    admin_password: Annotated[str, Arg(cli=False)]
    db_type: str | None = None
    apps: list[str] = field(default_factory=list)

    def run(self) -> None:
        self.require_production_privileges()
        self.fetch_missing_apps()
        self.create()

    @on_failure
    @on_cancel
    def remove_failed_site(self) -> dict:
        return {"site": self.name}

    @step("create", lambda self: f"Create site {self.name}")
    def create(self) -> None:
        Site.provision(
            self.bench,
            self.name,
            self.apps,
            self.admin_password,
            db_type=self.db_type,
            on_progress=self.report,
        )

    def fetch_missing_apps(self) -> None:
        """Fetch marketplace apps before Site.provision validates the app list."""
        installed = set(self.bench.registered_apps())
        missing = [name for name in self.apps if name not in installed]
        if not missing:
            return
        marketplace = Marketplace(self.bench)
        for app_name in missing:
            resolver = marketplace.find_app(app_name)
            with self.step("fetch", f"Fetch {app_name}"):
                App.from_repo(self.bench, resolver.repo, resolver.target).install(
                    install_dependencies=True, on_progress=self.report
                )


if __name__ == "__main__":
    NewSiteTask.main()
