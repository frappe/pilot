import subprocess
import sys

from .base_task import BaseTask


class BuildTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("--app", default=None)
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.app = args.app

    def run(self) -> None:
        self._step("build", f"Build assets for {self.app}" if self.app else "Build assets")
        argv = [*self.bench.frappe_call, "frappe", "build"]
        if self.app:
            argv += ["--app", self.app]
        result = subprocess.run(argv)
        if result.returncode != 0:
            sys.exit(result.returncode)
        self._step("done")


if __name__ == "__main__":
    BuildTask.main()
