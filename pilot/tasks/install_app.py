import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from pilot.tasks import Task, step

if TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.core.site import Site


@dataclass(kw_only=True)
class InstallAppTask(Task):
    command: ClassVar[str] = "install-app"
    # frappe writes module defs and doctypes before recording the app as
    # installed, so a kill mid-run leaves rows behind that fail every retry.
    is_cancellable_while_running: ClassVar[bool] = False

    site: str
    app: str

    def run(self) -> None:
        """Install the app on the site in maintenance mode."""
        app = self.bench.app(self.app)
        site = self.bench.site(self.site)
        with site.under_maintenance():
            dependencies = self.install(site, app)
            self.build_assets([app, *dependencies])
        site.clear_cache()
        self.bench.audit_action("app", {"event": "installed", "app": self.app, "site": self.site})

    @step("install", lambda self: f"Install {self.app} into {self.site}")
    def install(self, site: "Site", app: "App") -> list["App"]:
        """Install the app and its dependencies; return the dependencies."""
        from pilot.exceptions import BenchError

        try:
            return site.install_app_with_dependencies(app)
        except BenchError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)

    def build_assets(self, apps: list["App"]) -> None:
        """Build the assets of the apps."""
        from pilot.managers.environment import PythonEnvManager

        env = PythonEnvManager(self.bench)
        for dependency_app in apps:
            with self.step(
                f"assets_{dependency_app.config.name}",
                f"Build assets for {dependency_app.config.name}",
            ):
                env.build_assets_for_app(dependency_app)


if __name__ == "__main__":
    InstallAppTask.main()
