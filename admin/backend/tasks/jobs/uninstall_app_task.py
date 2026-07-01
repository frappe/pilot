import subprocess
import sys

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
        result = subprocess.run(
            [*self.bench.frappe_call, "frappe", "--site", self.site, "uninstall-app", self.app, "--yes", "--no-backup"]
        )
        if result.returncode != 0:
            sys.exit(result.returncode)
        self._step("done")


if __name__ == "__main__":
    UninstallAppTask.main()
