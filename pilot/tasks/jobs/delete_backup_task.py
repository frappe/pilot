from __future__ import annotations

import re

from pilot.tasks.jobs.base_task import BaseTask
from pilot.integrations.s3.backups import OffsiteBackup
from pilot.integrations.s3.base import S3IntegrationError

_TS_RE = re.compile(r"^(\d{8}_\d{6})")


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

    def delete_from_remote(self, filename: str) -> bool:
        match = _TS_RE.match(filename)
        if not match:
            print(f"Not found (skipping): {filename}")
            return False

        try:
            offsite_backup = OffsiteBackup.from_config(self.bench.config.s3, self.bench_root)
            offsite_backup.delete(self.site, match.group(1), filename)
            print(f"Deleted from S3: {filename}")
            return True
        except S3IntegrationError as e:
            print(f"Something seems wrong with s3 integration, skipping remote delete for {filename}: {e!s}")
            return False

    def run(self) -> None:
        self._step("delete", f"Delete {len(self.filenames)} backup(s) for {self.site}")
        backup_dir = self.bench_root / "sites" / self.site / "private" / "backups"
        deleted, offsite = [], False
        for filename in self.filenames:
            path = backup_dir / filename
            if path.is_file():
                path.unlink()
                deleted.append(filename)
                print(f"Deleted: {filename}")
            elif self.bench.config.s3.is_configured and self.delete_from_remote(filename):
                deleted.append(filename)
                offsite = True

        self._record(deleted, offsite)
        self._step("done")

    def _record(self, deleted: list[str], offsite: bool) -> None:
        self._record_audit(
            "backup",
            {"site": self.site, "event": "delete", "status": "success", "files": deleted, "offsite": offsite},
        )


if __name__ == "__main__":
    DeleteBackupTask.main()
