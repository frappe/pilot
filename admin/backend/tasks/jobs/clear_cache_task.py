import subprocess
import sys

from .base_task import BaseTask


class ClearCacheTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("site")
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.site = args.site

    def run(self) -> None:
        self._step("clear_cache", f"Clear cache for {self.site}")
        result = subprocess.run([*self.bench.frappe_call, "frappe", "--site", self.site, "clear-cache"])
        if result.returncode != 0:
            sys.exit(result.returncode)
        self._step("done")


if __name__ == "__main__":
    ClearCacheTask.main()
