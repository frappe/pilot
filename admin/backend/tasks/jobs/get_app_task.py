from pilot.commands.get_app import GetAppCommand
from pilot.exceptions import BenchError
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
            self._install_from_marketplace()
        else:
            self._step("fetch", f"Fetch {self.repo}")
            GetAppCommand(self.bench, self.repo, self.branch).run()
        self._step("done")

    def _install_from_marketplace(self) -> None:
        from pilot.core.marketplace import Marketplace

        apps = Marketplace(self.bench).read_all_apps()
        resolver = next((a for a in apps if a.app == self.marketplace_app), None)
        if not resolver:
            raise BenchError(f"'{self.marketplace_app}' not found in marketplace.")
        for dep in resolver.resolve():
            self._step("fetch", f"Fetch {dep.app}")
            GetAppCommand(self.bench, dep.repo, dep.target).run()


if __name__ == "__main__":
    GetAppTask.main()
