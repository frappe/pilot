from pilot.commands.get_app import GetAppCommand
from pilot.core.site import Site, SiteConfig
from pilot.exceptions import BenchError

from .base_task import BaseTask


class GetAndInstallAppTask(BaseTask):
    """Fetch an app (by repo or marketplace name) and install it on zero or
    more sites. Zero sites is valid: it just fetches (and, for marketplace
    apps, resolves dependencies) without installing anywhere."""

    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("--repo", default="")
        p.add_argument("--branch", default="")
        p.add_argument("--marketplace-app", default="")
        p.add_argument("--sites", nargs="*", default=[])
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.repo = args.repo
        self.branch = args.branch
        self.marketplace_app = args.marketplace_app
        self.sites = args.sites or []

    def run(self) -> None:
        cmds = self._fetch_from_marketplace() if self.marketplace_app else [self._fetch_custom()]
        self._install_on_sites(cmds)
        self._step("done")

    def _fetch_custom(self) -> GetAppCommand:
        self._step("fetch", f"Fetch {self.repo}")
        cmd = GetAppCommand(self.bench, self.repo, self.branch)
        cmd.run()
        return cmd

    def _fetch_from_marketplace(self) -> list[GetAppCommand]:
        from pilot.core.marketplace import Marketplace

        apps = Marketplace(self.bench).read_all_apps()
        resolver = next((a for a in apps if a.app == self.marketplace_app), None)
        if not resolver:
            raise BenchError(f"'{self.marketplace_app}' not found in marketplace.")
        cmds = []
        for dep in resolver.resolve():
            self._step("fetch", f"Fetch {dep.app}")
            cmd = GetAppCommand(self.bench, dep.repo, dep.target)
            cmd.run()
            cmds.append(cmd)
        return cmds

    def _install_on_sites(self, cmds: list[GetAppCommand]) -> None:
        from pilot.managers.python_env_manager import PythonEnvManager

        for site in self.sites:
            safe_key = site.replace(".", "_").replace("-", "_")
            for cmd in cmds:
                self._step(f"install_{safe_key}_{cmd.app.config.name}", f"Install {cmd.app.config.name} on {site}")
                Site(SiteConfig(name=site, apps=[]), self.bench).install_app(cmd.app)

        env = PythonEnvManager(self.bench)
        for cmd in cmds:
            self._step("build", f"Build assets for {cmd.app.config.name}")
            env.build_assets_for_app(cmd.app)


if __name__ == "__main__":
    GetAndInstallAppTask.main()
