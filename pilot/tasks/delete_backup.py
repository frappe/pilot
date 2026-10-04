from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import ClassVar

from pilot.core.site import Site
from pilot.core.site.backups import parse_backup_timestamp
from pilot.integrations.s3.backups import OffsiteBackup
from pilot.integrations.s3.base import S3IntegrationError
from pilot.tasks import Task, step


@dataclass(kw_only=True)
class DeleteBackupTask(Task):
    command: ClassVar[str] = "delete-backup"

    site: str
    filenames: list[str]

    @cached_property
    def site_record(self) -> Site:
        return self.bench.site(self.site)

    def run(self) -> None:
        self.delete()

    def delete_from_remote(self, filename: str) -> bool:
        timestamp = parse_backup_timestamp(filename)
        if not timestamp:
            print(f"Not found (skipping): {filename}")
            return False

        try:
            offsite_backup = OffsiteBackup.from_config(self.bench.config.s3, self.bench_root)
            if filename not in (offsite_backup.get_backup(self.site, timestamp) or {}).values():
                return False
            offsite_backup.delete(self.site, timestamp, filename)
            print(f"Deleted from S3: {filename}")
            return True
        except S3IntegrationError as e:
            print(f"Something seems wrong with s3 integration, skipping remote delete for {filename}: {e!s}")
            return False

    @step("delete", lambda self: f"Delete {len(self.filenames)} backup(s) for {self.site}")
    def delete(self) -> None:
        backups = self.site_record.backups
        deleted, offsite = [], False
        for filename in self.filenames:
            path = backups.resolve_file(filename)
            removed = path.is_file()
            if removed:
                path.unlink()
                print(f"Deleted: {filename}")
            if self.bench.config.s3.is_configured and self.delete_from_remote(filename):
                removed = offsite = True
            if removed:
                deleted.append(filename)

        self.record(deleted, offsite)

    def record(self, deleted: list[str], offsite: bool) -> None:
        self.bench.audit_action(
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
