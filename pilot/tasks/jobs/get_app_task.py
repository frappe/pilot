from pilot.commands.get_app import GetAppCommand
from .base_task import BaseTask
from .marketplace_fetcher import MarketplaceFetcher


class GetAppTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("--repo", default="")
        p.add_argument("--branch", default="")
        p.add_argument("--marketplace-app", default="")
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.repo = args.repo
        self.branch = args.branch
        self.marketplace_app = args.marketplace_app

    def run(self) -> None:
        if self.marketplace_app:
            MarketplaceFetcher(self.bench, self._step).fetch(self.marketplace_app)
        else:
            self._step("fetch", f"Fetch {self.repo}")
            GetAppCommand(self.bench, self.repo, self.branch).run()
        self._step("done")


if __name__ == "__main__":
    GetAppTask.main()
