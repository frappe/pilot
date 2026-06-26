from pilot.commands.remove_app import RemoveAppCommand
from .base_task import BaseTask


class RemoveAppTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("name")
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.name = args.name

    def run(self) -> None:
        RemoveAppCommand(self.bench, self.name, skip_confirm=True, force=True).run()


if __name__ == "__main__":
    RemoveAppTask.main()
