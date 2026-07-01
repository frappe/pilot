import subprocess
import sys

from .base_task import BaseTask


class BackupSiteTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("site")
        p.add_argument("--with-files", action="store_true")
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.site = args.site
        self.with_files = args.with_files

    def run(self) -> None:
        self._step("backup", f"Backup site {self.site}")
        argv = [*self.bench.frappe_call, "frappe", "--site", self.site, "backup"]
        if self.with_files:
            argv.append("--with-files")
        result = subprocess.run(argv)
        if result.returncode != 0:
            sys.exit(result.returncode)
        self._step("done")


if __name__ == "__main__":
    BackupSiteTask.main()
