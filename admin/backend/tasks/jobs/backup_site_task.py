import subprocess
import sys
from pathlib import Path

from .base_task import BaseTask
from pilot.integrations.s3.backups import OffsiteBackup
from pilot.integrations.s3.base import S3IntegrationError


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

    def _backups_path(self) -> Path:
        return self.bench.sites_path / self.site / "private" / "backups"

    def _latest_backup(self) -> tuple[str, list[Path]]:
        """Timestamp and files of the most recent backup run for this site: the
        database dump and site config always, plus the files/private-files
        archives when --with-files was used. Frappe names them
        `<timestamp>-<site>-<part>.<ext>`, so files sharing the latest timestamp
        prefix belong to the same backup."""
        groups: dict[str, list[Path]] = {}
        for path in self._backups_path().glob("*"):
            timestamp = path.name.split("-", 1)[0]
            groups.setdefault(timestamp, []).append(path)
        latest_timestamp = max(groups)
        return latest_timestamp, groups[latest_timestamp]

    def run(self) -> None:
        self._step("backup", f"Backup site {self.site}")
        argv = [*self.bench.frappe_call, "frappe", "--site", self.site, "backup"]
        if self.with_files:
            argv.append("--with-files")
        result = subprocess.run(argv)
        if result.returncode != 0:
            sys.exit(result.returncode)

        if self.bench.config.s3.is_configured:
            try:
                offsite_backup = OffsiteBackup.from_config(self.bench.config.s3)
                self._step("backup_upload", "Uploading backup to S3")
                timestamp, backup_files = self._latest_backup()

                for backup_file in backup_files:
                    offsite_backup.upload(self.site, timestamp, backup_file)

            except S3IntegrationError as e:
                print(f"Something seems wrong with s3 integration currently skipping remote upload: {e!s}")
                self._step("done")
                return

        self._step("done")


if __name__ == "__main__":
    BackupSiteTask.main()
