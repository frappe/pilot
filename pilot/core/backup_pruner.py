"""Apply a retention policy to one site's backups, local and offsite."""

import re

from pilot.config.site_backup_config import read_retention
from pilot.core.backup_retention import BackupRetentionPolicy
from pilot.integrations.s3.backups import OffsiteBackup

_TS_RE = re.compile(r"^(\d{8}_\d{6})")


class BackupPruner:
    def __init__(self, bench, site: str) -> None:
        self.bench = bench
        self.site = site
        self._site_dir = bench.sites_path / site
        self._backups_dir = self._site_dir / "private" / "backups"

    def prune(self) -> list[str]:
        """Delete runs the site's retention policy rejects, returning the timestamps
        actually pruned. With no per-site retention (automated backups off), keep all."""
        config = read_retention(self._site_dir / "site_config.json")
        if config is None:
            return []

        offsite = self._offsite()
        offsite_runs = offsite.list_backups(self.site) if offsite else {}
        timestamps = sorted(set(self._local_timestamps()) | set(offsite_runs))

        policy = BackupRetentionPolicy(config)
        return [ts for ts in policy.select_deletions(timestamps) if self._delete_run(offsite, offsite_runs, ts)]

    def _delete_run(self, offsite, offsite_runs: dict, timestamp: str) -> bool:
        """Delete one run offsite-first, then local. On an offsite error the run is
        left intact in both stores (retried next prune) rather than half-deleted, and
        the timestamp is not reported as pruned."""
        if timestamp in offsite_runs:
            try:
                self._delete_offsite(offsite, timestamp, offsite_runs[timestamp])
            except Exception as error:
                print(f"Kept backup {timestamp}: offsite delete failed: {error}")
                return False
        self._delete_local(timestamp)
        return True

    def _local_timestamps(self) -> list[str]:
        if not self._backups_dir.is_dir():
            return []
        return [m.group(1) for f in self._backups_dir.iterdir() if f.is_file() and (m := _TS_RE.match(f.name))]

    def _delete_local(self, timestamp: str) -> None:
        if not self._backups_dir.is_dir():
            return
        for f in self._backups_dir.glob(f"{timestamp}-*"):
            f.unlink(missing_ok=True)

    def _delete_offsite(self, offsite: OffsiteBackup, timestamp: str, files: dict[str, str]) -> None:
        for filename in list(files.values()):
            offsite.delete(self.site, timestamp, filename)

    def _offsite(self) -> OffsiteBackup | None:
        if not self.bench.config.s3.is_configured:
            return None
        return OffsiteBackup.from_config(self.bench.config.s3, self.bench.path)
