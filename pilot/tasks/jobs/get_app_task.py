from pilot.commands.apps.download import GetAppCommand
from pilot.integrations.marketplace import Marketplace

from .base_task import BaseTask


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
            resolver = Marketplace(self.bench).find_app(self.marketplace_app)
            repo, branch = resolver.repo, resolver.target
        else:
            repo, branch = self.repo, self.branch
        self._step("fetch", f"Fetch {self.marketplace_app or self.repo}")
        GetAppCommand(self.bench, repo, branch, install_dependencies=bool(self.marketplace_app)).run()
        self._step("done")


if __name__ == "__main__":
    GetAppTask.main()
