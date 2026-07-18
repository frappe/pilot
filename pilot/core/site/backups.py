"""Apply a retention policy to one site's backups, local and offsite."""

import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config.site_backup import clear_retention, read_retention, write_retention
from pilot.core.site.retention import BackupRetentionPolicy
from pilot.integrations.s3.backups import OffsiteBackup

if TYPE_CHECKING:
    from pilot.core.site import Site

_TS_RE = re.compile(r"^(\d{8}_\d{6})")


def parse_backup_timestamp(filename: str) -> str | None:
    """Return a Frappe backup filename's YYYYMMDD_HHMMSS prefix."""
    match = _TS_RE.match(filename)
    return match.group(1) if match else None


class SiteBackups:
    def __init__(self, site: "Site") -> None:
        self.site = site

    @property
    def directory(self) -> Path:
        return self.site.path / "private" / "backups"

    def latest_run(self) -> tuple[str, list[Path]]:
        """Timestamp and files of the most recently created backup run for this site."""
        groups: dict[str, list[Path]] = {}
        if self.directory.is_dir():
            for path in self.directory.iterdir():
                timestamp = parse_backup_timestamp(path.name)
                if timestamp:
                    groups.setdefault(timestamp, []).append(path)
        if not groups:
            return "", []
        latest = max(groups)
        return latest, groups[latest]

    def prune(self) -> list[str]:
        """Delete runs rejected by this site's retention policy."""
        config = read_retention(self.site.path / "site_config.json")
        if config is None:
            return []

        offsite = self._offsite()
        offsite_runs = offsite.list_backups(self.site.config.name) if offsite else {}
        timestamps = sorted(set(self._local_timestamps()) | set(offsite_runs))

        policy = BackupRetentionPolicy(config)
        return [
            ts
            for ts in policy.select_deletions(timestamps)
            if self._delete_run(offsite, offsite_runs, ts)
        ]

    def download_file_path(self, timestamp: str, file_id: str) -> Path:
        from pilot.exceptions import BenchError

        if (
            not file_id.startswith(timestamp)
            or "/" in file_id
            or "\\" in file_id
            or file_id.startswith(".")
        ):
            raise BenchError("Backup filename is invalid.")

        backups_dir = self.directory.resolve()
        target = (backups_dir / file_id).resolve()
        if backups_dir not in target.parents or not target.is_file():
            raise FileNotFoundError(file_id)
        return target

    def download_links(self, timestamp: str) -> dict:
        offsite = OffsiteBackup.from_config(self.site.bench.config.s3, self.site.bench.path)
        files = offsite.get_backup(self.site.config.name, timestamp)
        if not files:
            raise FileNotFoundError(timestamp)
        return {
            kind: offsite.presigned_url(self.site.config.name, timestamp, filename)
            for kind, filename in files.items()
        }

    def schedule(self) -> dict:
        from pilot.managers.cron import CronManager

        schedule = CronManager(self.site.bench.path).get_schedule(self.site.config.name)
        retention = read_retention(self.site.path / "site_config.json")
        return {"schedule": schedule, "retention": asdict(retention) if retention else None}

    def set_schedule(self, schedule: str, retention) -> dict:
        from pilot.managers.cron import CronManager

        CronManager(self.site.bench.path).set_schedule(
            self.site.config.name,
            schedule,
            self._cron_command(),
        )
        write_retention(self.site.path / "site_config.json", retention)
        return self.schedule()

    def clear_schedule(self) -> None:
        from pilot.managers.cron import CronManager

        CronManager(self.site.bench.path).remove_schedule(self.site.config.name)
        clear_retention(self.site.path / "site_config.json")

    def retention_from_payload(self, block: dict | None):
        return retention_from_payload(block)

    def _delete_run(self, offsite, offsite_runs: dict, timestamp: str) -> bool:
        """Delete one run offsite first; keep local files if offsite deletion fails."""
        if timestamp in offsite_runs:
            try:
                self._delete_offsite(offsite, timestamp, offsite_runs[timestamp])
            except Exception as error:
                print(f"Kept backup {timestamp}: offsite delete failed: {error}")
                return False
        self._delete_local(timestamp)
        return True

    def _local_timestamps(self) -> list[str]:
        if not self.directory.is_dir():
            return []
        return [
            timestamp
            for file in self.directory.iterdir()
            if file.is_file() and (timestamp := parse_backup_timestamp(file.name))
        ]

    def _delete_local(self, timestamp: str) -> None:
        if not self.directory.is_dir():
            return
        for file in self.directory.glob(f"{timestamp}-*"):
            file.unlink(missing_ok=True)

    def _delete_offsite(self, offsite: OffsiteBackup, timestamp: str, files: dict[str, str]) -> None:
        for filename in list(files.values()):
            offsite.delete(self.site.config.name, timestamp, filename)

    def _offsite(self) -> OffsiteBackup | None:
        if not self.site.bench.config.s3.is_configured:
            return None
        return OffsiteBackup.from_config(self.site.bench.config.s3, self.site.bench.path)

    def _cron_command(self) -> str:
        log_file = self.site.bench.logs_path / f"backup-{self.site.config.name}.log"
        return (
            f"{sys.executable} -m pilot.tasks.backup_site {self.site.bench.path} "
            f"{self.site.config.name} --with-files >> {log_file} 2>&1"
        )


def retention_from_payload(block: dict | None):
    from pilot.config import VALID_SCHEMES, BackupConfig

    block = block or {}
    config = BackupConfig()
    scheme = str(block.get("scheme", config.scheme)).strip()
    if scheme not in VALID_SCHEMES:
        return f"Retention scheme must be one of: {', '.join(VALID_SCHEMES)}."
    config.scheme = scheme
    for key in config.counts:
        if key not in block:
            continue
        try:
            value = int(block[key])
        except (TypeError, ValueError):
            return f"{key} must be a whole number."
        if value < 0:
            return f"{key} must be zero or more."
        setattr(config, key, value)
    return config
