import sys

from pilot.config.site_config import SiteConfig
from pilot.core.site import Site

from .base_task import BaseTask


class ReinstallSiteTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("site")
        p.add_argument("--admin-password", default="admin")
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.site = args.site
        self.admin_password = args.admin_password

    def run(self) -> None:
        print(f"Reinstalling site '{self.site}'...")
        sys.stdout.flush()
        site = Site(SiteConfig(name=self.site, apps=[]), self.bench)
        site.reinstall(self.admin_password)
        print(f"\nSite '{self.site}' reinstalled.")


if __name__ == "__main__":
    ReinstallSiteTask.main()
