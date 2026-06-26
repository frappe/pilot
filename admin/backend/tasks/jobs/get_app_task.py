from pilot.commands.get_app import GetAppCommand
from .base_task import BaseTask


class GetAppTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("repo")
        p.add_argument("--branch", default="")
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.repo = args.repo
        self.branch = args.branch

    def run(self) -> None:
        GetAppCommand(self.bench, self.repo, self.branch).run()


if __name__ == "__main__":
    GetAppTask.main()
