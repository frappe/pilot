import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks.base import BaseTask, step


@dataclass(kw_only=True)
class InstallAppTask(BaseTask):
    command: ClassVar[str] = "install-app"

    site: str
    app: str

    def run(self) -> None:
        from pilot.core.site import Site

        app = self.bench.app(self.app)
        site = Site.for_name(self.site, self.bench)
        dependencies = self.install(site, app)
        self.build_assets([app, *dependencies])

    @step("install", lambda self: f"Install {self.app} into {self.site}")
    def install(self, site, app):
        from pilot.exceptions import BenchError

        try:
            return site.install_app_with_dependencies(app)
        except BenchError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)

    def build_assets(self, apps) -> None:
        from pilot.managers.python_environment import PythonEnvManager

        env = PythonEnvManager(self.bench)
        for dependency_app in apps:
            with self.step(
                f"assets_{dependency_app.config.name}",
                f"Build assets for {dependency_app.config.name}",
            ):
                env.build_assets_for_app(dependency_app)


if __name__ == "__main__":
    InstallAppTask.main()
