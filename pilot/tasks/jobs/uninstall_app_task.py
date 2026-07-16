from pilot.commands.apps.uninstall import UninstallAppCommand

from pilot.tasks.jobs.base_task import BaseTask


class UninstallAppTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("site")
        p.add_argument("app")
        p.add_argument("--force", action="store_true")
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.site = args.site
        self.app = args.app
        self.force = args.force

    def run(self) -> None:
        self._step("uninstall", f"Uninstall {self.app} from {self.site}")
        UninstallAppCommand(self.bench, self.site, [self.app], force=self.force).run()
        self._step("done")


if __name__ == "__main__":
    UninstallAppTask.main()
