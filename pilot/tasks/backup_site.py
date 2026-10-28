import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path
from typing import ClassVar

from pilot.core.site import Site
from pilot.exceptions import BenchError
from pilot.integrations.s3.backups import OffsiteBackup
from pilot.integrations.s3.base import S3IntegrationError
from pilot.tasks import Task, step


@dataclass(kw_only=True)
class BackupSiteTask(Task):
    command: ClassVar[str] = "backup-site"
    timeout_seconds: ClassVar[int] = 86400

    site: str
    with_files: bool = False
    automatic: bool = False

    @cached_property
    def site_record(self) -> Site:
        return self.bench.site(self.site)

    def run(self) -> None:
        if self.automatic:
            options = self.site_record.backups.get_scheduled_backup(datetime.now(UTC))
            if options is None:
                return
            self.with_files = options["with_files"]
        self.backup()

    @step("backup", lambda self: f"Backup site {self.site}")
    def backup(self) -> None:
        try:
            timestamp, files, offsite = self.take()
        except (BenchError, OSError) as error:
            print(error)
            self.record(status="failed", timestamp="", files={}, offsite=False, pruned=[])
            self.notify_if_failing()
            sys.exit(getattr(error, "returncode", None) or 1)
        pruned = self.prune()
        self.record(status="success", timestamp=timestamp, files=files, offsite=offsite, pruned=pruned)

    def take(self) -> tuple[str, dict[str, int], bool]:
        """Stream into S3 when the site supports it, otherwise back up to disk and upload."""
        backups = self.site_record.backups
        if backups.is_streaming_supported:
            timestamp, files = backups.stream(with_files=self.with_files)
            return timestamp, files, True
        timestamp, backup_files = backups.take(with_files=self.with_files)
        files = {path.name: path.stat().st_size for path in backup_files}
        return timestamp, files, self.upload(timestamp, backup_files)

    def notify_if_failing(self) -> None:
        from pilot.core.notification.events import backups_failing
        from pilot.core.site.backups import MAX_DAILY_FAILURES

        failures = self.site_record.backups.get_failures_since_last_success(datetime.now(UTC))
        if failures == MAX_DAILY_FAILURES:
            backups_failing(self.bench, self.site, failures)

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
        self.bench.audit_action(
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
