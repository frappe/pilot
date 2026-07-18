from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks.base import BaseTask, step
from pilot.core.backup_pruning import BackupPruner, parse_backup_timestamp
from pilot.integrations.s3.backups import OffsiteBackup
from pilot.integrations.s3.base import S3IntegrationError


@dataclass(kw_only=True)
class DeleteBackupTask(BaseTask):
    command: ClassVar[str] = "delete-backup"

    site: str
    filenames: list[str]

    def run(self) -> None:
        self.delete()

    def delete_from_remote(self, filename: str) -> bool:
        timestamp = parse_backup_timestamp(filename)
        if not timestamp:
            print(f"Not found (skipping): {filename}")
            return False

        try:
            offsite_backup = OffsiteBackup.from_config(self.bench.config.s3, self.bench_root)
            offsite_backup.delete(self.site, timestamp, filename)
            print(f"Deleted from S3: {filename}")
            return True
        except S3IntegrationError as e:
            print(
                f"Something seems wrong with s3 integration, skipping remote delete for {filename}: {e!s}"
            )
            return False

    @step("delete", lambda self: f"Delete {len(self.filenames)} backup(s) for {self.site}")
    def delete(self) -> None:
        backup_dir = BackupPruner(self.bench, self.site).backups_dir
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

        self.record(deleted, offsite)

    def record(self, deleted: list[str], offsite: bool) -> None:
        self.record_audit(
            "backup",
            {
                "site": self.site,
                "event": "delete",
                "status": "success",
                "files": deleted,
                "offsite": offsite,
            },
        )


if __name__ == "__main__":
    DeleteBackupTask.main()
