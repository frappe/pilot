import json

from admin.backend.readers.app_reader import AppReader
from admin.backend.readers.site_reader import SiteReader

from .base_task import BaseTask


class FetchAppUpdatesTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("site")
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.site_name = args.site

    def run(self) -> None:
        site = SiteReader(self.bench_root).read_one(self.site_name)
        updates = AppReader(self.bench_root).check_remote_updates(site.installed_apps)
        print(json.dumps(updates), flush=True)


if __name__ == "__main__":
    FetchAppUpdatesTask.main()
