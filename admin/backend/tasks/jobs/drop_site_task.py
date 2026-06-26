from pilot.commands.drop_site import DropSiteCommand
from .base_task import BaseTask


class DropSiteTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("name")
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.name = args.name

    def run(self) -> None:
        DropSiteCommand(self.bench, self.name).run()


if __name__ == "__main__":
    DropSiteTask.main()
