from pilot.core.app import App
from pilot.integrations.marketplace import Marketplace

from pilot.tasks.jobs.base_task import BaseTask


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
        app = App.from_repo(self.bench, repo, branch)
        app.install(install_dependencies=bool(self.marketplace_app), on_progress=self._report)
        self._step("done")


if __name__ == "__main__":
    GetAppTask.main()
