"""Apply a retention policy to one site's backups, local and offsite."""

import re
from pathlib import Path

from pilot.core.backup_retention import BackupRetentionPolicy
from pilot.integrations.s3.backups import OffsiteBackup

_TS_RE = re.compile(r"^(\d{8}_\d{6})")


class BackupPruner:
    def __init__(self, bench, site: str) -> None:
        self.bench = bench
        self.site = site
        self._backups_dir = bench.sites_path / site / "private" / "backups"

    def prune(self) -> list[str]:
        """Delete runs the policy rejects, returning the pruned timestamps."""
        offsite = self._offsite()
        offsite_runs = offsite.list_backups(self.site) if offsite else {}
        timestamps = sorted(set(self._local_timestamps()) | set(offsite_runs))

        policy = BackupRetentionPolicy(self.bench.config.backup)
        deletions = policy.select_deletions(timestamps)

        for timestamp in deletions:
            self._delete_local(timestamp)
            if timestamp in offsite_runs:
                self._delete_offsite(offsite, timestamp, offsite_runs[timestamp])
        return deletions

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
        return OffsiteBackup.from_config(self.bench.config.s3)
