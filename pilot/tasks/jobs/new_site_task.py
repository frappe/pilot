from pilot.commands.new_site import NewSiteCommand
from .base_task import BaseTask
from .marketplace_fetcher import MarketplaceFetcher


class NewSiteTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("name")
        p.set_defaults(admin_password=None)
        p.add_argument("--db-type", default=None)
        p.add_argument("--apps", nargs="*", default=[])
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.name = args.name
        self.admin_password = args.admin_password
        self.db_type = args.db_type
        self.apps = args.apps

    def run(self) -> None:
        self._require_production_privileges()
        self._fetch_missing_apps()
        self._step("create", f"Create site {self.name}")
        NewSiteCommand(self.bench, self.name, self.apps, self.admin_password, db_type=self.db_type).run()
        self._step("done")

    def _fetch_missing_apps(self) -> None:
        """The new-site wizard offers marketplace apps that may not be cloned onto
        this bench yet; fetch those before NewSiteCommand validates the app list."""
        apps_txt = self.bench.sites_path / "apps.txt"
        installed = set(apps_txt.read_text().splitlines()) if apps_txt.exists() else set()
        fetcher = MarketplaceFetcher(self.bench, self._step)
        for app_name in self.apps:
            if app_name not in installed:
                fetcher.fetch(app_name)


if __name__ == "__main__":
    NewSiteTask.main()
