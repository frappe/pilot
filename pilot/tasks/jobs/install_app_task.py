import sys

from pilot.tasks.jobs.base_task import BaseTask


class InstallAppTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("site")
        p.add_argument("app")
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.site = args.site
        self.app = args.app

    def run(self) -> None:
        from pilot.config.site_config import SiteConfig
        from pilot.core.app_validator.dependency_declarations import DependencyDeclarationsCheck
        from pilot.core.site import Site
        from pilot.exceptions import BenchError
        from pilot.managers.python_environment import PythonEnvManager

        app = self.bench.app(self.app)
        site = Site(SiteConfig(name=self.site, apps=[]), self.bench)

        self._step("install", f"Install {self.app} into {self.site}")
        try:
            site.install_app(app)
        except BenchError as exc:
            print(str(exc), file=sys.stderr)
            sys.exit(1)

        # site.install_app cascades installing hooks.py's required_apps onto
        # the site itself, but never builds their assets — do that here.
        required = DependencyDeclarationsCheck()._get_hooks_required_apps(app)
        apps = [app]
        for name in required:
            try:
                apps.append(self.bench.app(name))
            except BenchError:
                continue

        env = PythonEnvManager(self.bench)
        for app in apps:
            self._step(f"assets_{app.config.name}", f"Build assets for {app.config.name}")
            env.build_assets_for_app(app)

        self._step("done")


if __name__ == "__main__":
    InstallAppTask.main()
