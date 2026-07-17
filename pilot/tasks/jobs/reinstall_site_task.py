from pilot.config.site_config import SiteConfig
from pilot.core.site import Site

from pilot.tasks.jobs.base_task import BaseTask


class ReinstallSiteTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("site")
        p.set_defaults(admin_password=None)
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.site = args.site
        self.admin_password = args.admin_password

    def run(self) -> None:
        self._step("reinstall", f"Reinstall site {self.site}")
        site = Site(SiteConfig(name=self.site, apps=[]), self.bench)
        site.reinstall(self.admin_password)
        self._step("done")


if __name__ == "__main__":
    ReinstallSiteTask.main()
