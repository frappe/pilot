from pilot.commands.uninstall_app import UninstallAppCommand

from .base_task import BaseTask


class UninstallAppTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("site")
        p.add_argument("app")
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.site = args.site
        self.app = args.app

    def run(self) -> None:
        self._step("uninstall", f"Uninstall {self.app} from {self.site}")
        UninstallAppCommand(self.bench, self.site, [self.app]).run()
        self._step("done")


if __name__ == "__main__":
    UninstallAppTask.main()
