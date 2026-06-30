import time

from pilot.commands.get_app import GetAppCommand
from pilot.core.site import Site, SiteConfig
from pilot.exceptions import BenchError

from .base_task import BaseTask


def _step(key: str, label: str = "") -> None:
    print(f"##[step:{key},{time.time():.3f}] {label}", flush=True)


class GetAndInstallAppTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("site")
        p.add_argument("app")
        p.add_argument("--repo", default="")
        p.add_argument("--branch", default="")
        p.add_argument("--marketplace-app", default="")
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.site = args.site
        self.app = args.app
        self.repo = args.repo
        self.branch = args.branch
        self.marketplace_app = args.marketplace_app

    def run(self) -> None:
        if self.marketplace_app:
            self._install_from_marketplace()
        else:
            _step("fetch", f"Fetch {self.app}")
            cmd = GetAppCommand(self.bench, self.repo, self.branch)
            cmd.run()
            _step("install", f"Install on {self.site}")
            Site(SiteConfig(name=self.site, apps=[]), self.bench).install_app(cmd.app)
            _step("done")

    def _install_from_marketplace(self) -> None:
        from pilot.core.marketplace import Marketplace

        apps = Marketplace(self.bench).read_all_apps()
        resolver = next((a for a in apps if a.app == self.marketplace_app), None)
        if not resolver:
            raise BenchError(f"'{self.marketplace_app}' not found in marketplace.")
        site = Site(SiteConfig(name=self.site, apps=[]), self.bench)
        for dep in resolver.resolve():
            _step("fetch", f"Fetch {dep.app}")
            cmd = GetAppCommand(self.bench, dep.repo, dep.target)
            cmd.run()
            _step("install", f"Install {dep.app} on {self.site}")
            site.install_app(cmd.app)
        _step("done")


if __name__ == "__main__":
    GetAndInstallAppTask.main()
