from pilot.commands.apps.download import GetAppCommand
from pilot.core.app import App
from pilot.core.site import Site, SiteConfig
from pilot.integrations.marketplace import Marketplace

from pilot.tasks.jobs.base_task import BaseTask


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
        cmd = self._fetch()
        # Frappe's site install-app cascades installing declared dependencies
        # onto the site itself, but never builds their assets — that's a
        # separate, bench-wide step this task still owns.
        self._install_on_sites(cmd.app)
        self._build_assets([cmd.app] + cmd.installed_dependencies)
        self._step("done")

    def _fetch(self) -> GetAppCommand:
        # get-app resolves and installs marketplace dependencies itself; a
        # plain repo has none to resolve.
        if self.marketplace_app:
            resolver = Marketplace(self.bench).find_app(self.marketplace_app)
            repo, branch = resolver.repo, resolver.target
        else:
            repo, branch = self.repo, self.branch
        self._step("fetch", f"Fetch {self.marketplace_app or self.repo}")
        cmd = GetAppCommand(
            self.bench,
            repo,
            branch,
            install_dependencies=bool(self.marketplace_app),
            skip_validations=False,
        )
        cmd.run()
        return cmd

    def _install_on_sites(self, app: App) -> None:
        for site in self.sites:
            safe_key = site.replace(".", "_").replace("-", "_")
            self._step(
                f"install_{safe_key}_{app.config.name}", f"Install {app.config.name} on {site}"
            )
            Site(SiteConfig(name=site, apps=[]), self.bench).install_app(app)

    def _build_assets(self, apps: list[App]) -> None:
        from pilot.managers.python_environment import PythonEnvManager

        env = PythonEnvManager(self.bench)
        for app in apps:
            self._step(f"build_{app.config.name}", f"Build assets for {app.config.name}")
            env.build_assets_for_app(app)


if __name__ == "__main__":
    GetAndInstallAppTask.main()
