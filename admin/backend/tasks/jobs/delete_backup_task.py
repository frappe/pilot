from __future__ import annotations

from .base_task import BaseTask


class DeleteBackupTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("site")
        p.add_argument("filenames", nargs="+")
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.site = args.site
        self.filenames = args.filenames

    def run(self) -> None:
        self._step("delete", f"Delete {len(self.filenames)} backup(s) for {self.site}")
        backup_dir = self.bench_root / "sites" / self.site / "private" / "backups"
        for filename in self.filenames:
            path = backup_dir / filename
            if path.is_file():
                path.unlink()
                print(f"Deleted: {filename}")
            else:
                print(f"Not found (skipping): {filename}")
        self._step("done")


if __name__ == "__main__":
    DeleteBackupTask.main()
