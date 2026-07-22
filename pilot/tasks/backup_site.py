import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path
from typing import ClassVar

from pilot.core.site import Site
from pilot.integrations.s3.backups import OffsiteBackup
from pilot.integrations.s3.base import S3IntegrationError
from pilot.tasks import Task, step


@dataclass(kw_only=True)
class BackupSiteTask(Task):
    command: ClassVar[str] = "backup-site"

    site: str
    with_files: bool = False

    @cached_property
    def site_record(self) -> Site:
        return self.bench.site(self.site)

    def run(self) -> None:
        self.backup()

    @step("backup", lambda self: f"Backup site {self.site}")
    def backup(self) -> None:
        # cwd matters: frappe's bench helper reads apps.txt from the current dir.
        # The task wrapper sets this, but cron invokes us directly, so set it here.
        argv = [*self.bench.frappe_call, "frappe", "--site", self.site, "backup"]
        if self.with_files:
            argv.append("--with-files")
        result = subprocess.run(argv, cwd=str(self.bench.sites_path))
        if result.returncode != 0:
            self.record(status="failed", timestamp="", files={}, offsite=False, pruned=[])
            sys.exit(result.returncode)

        timestamp, backup_files = self.site_record.backups.latest_run()
        if not backup_files:
            print("Backup command exited 0 but produced no files.")
            self.record(status="failed", timestamp="", files={}, offsite=False, pruned=[])
            sys.exit(1)
        files = {path.name: path.stat().st_size for path in backup_files}
        offsite = self.upload(timestamp, backup_files)
        pruned = self.prune()
        self.record(status="success", timestamp=timestamp, files=files, offsite=offsite, pruned=pruned)

    def upload(self, timestamp: str, backup_files: list[Path]) -> bool:
        if not self.bench.config.s3.is_configured:
            return False
        return self.upload_to_s3(timestamp, backup_files)

    @step("backup_upload", "Uploading backup to S3")
    def upload_to_s3(self, timestamp: str, backup_files: list[Path]) -> bool:
        try:
            offsite_backup = OffsiteBackup.from_config(self.bench.config.s3, self.bench_root)
            for backup_file in backup_files:
                offsite_backup.upload(self.site, timestamp, backup_file)
            return True
        except S3IntegrationError as e:
            print(f"Something seems wrong with s3 integration currently skipping remote upload: {e!s}")
            return False

    @step("retention", "Applying backup retention")
    def prune(self) -> list[str]:
        """Retention runs after every backup; a failure here must not fail the backup."""
        try:
            return self.site_record.backups.prune()
        except Exception as e:
            print(f"Backup retention skipped due to error: {e!s}")
            return []

    def record(self, status: str, timestamp: str, files: dict, offsite: bool, pruned: list[str]) -> None:
        self.record_audit(
            "backup",
            {
                "site": self.site,
                "event": "backup",
                "timestamp": timestamp,
                "finished_at": datetime.now(UTC).isoformat(),
                "status": status,
                "with_files": self.with_files,
                "files": files,
                "offsite": offsite,
                "pruned": pruned,
            },
        )


if __name__ == "__main__":
    BackupSiteTask.main()
