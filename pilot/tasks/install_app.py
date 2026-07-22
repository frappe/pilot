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

    site: str
    app: str

    def run(self) -> None:
        app = self.bench.app(self.app)
        site = self.bench.site(self.site)
        dependencies = self.install(site, app)
        self.build_assets([app, *dependencies])

    @step("install", lambda self: f"Install {self.app} into {self.site}")
    def install(self, site: "Site", app: "App") -> list["App"]:
        from pilot.exceptions import BenchError

        try:
            return site.install_app_with_dependencies(app)
        except BenchError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)

    def build_assets(self, apps: list["App"]) -> None:
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
