from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pilot.core.bench import Bench
from pilot.core.site.backups import parse_backup_timestamp
from pilot.integrations.s3.backups import OffsiteBackup

_OFFSITE_FILE_KINDS = {
    "database": "database",
    "files": "public-file",
    "private_files": "private-file",
    "site_config": "site_config",
}


@dataclass
class BackupFile:
    filename: str
    path: str
    size_bytes: int
    created_at: datetime
    kind: str
    timestamp: str


@dataclass
class Backup:
    """Every file produced by one `frappe backup` invocation, identified by its timestamp."""

    timestamp: str
    created_at: datetime
    files: list[BackupFile]
    is_offsite: bool = False


class BackupProvider:
    """A site's backups, local (on disk) and offsite (S3), merged into one timeline."""

    def __init__(self, bench_root: Path, site_name: str) -> None:
        self._site_name = site_name
        self._bench = Bench(bench_root)
        self._site = self._bench.site(site_name)

    def get_all(self, limit: int | None = None) -> list[Backup]:
        backups = self.merge_backups(self.local_backups, self.get_offsite_backups(limit))
        ordered = sorted(backups.values(), key=lambda backup: backup.timestamp, reverse=True)
        return ordered[:limit] if limit is not None else ordered

    @property
    def local_backups(self) -> dict[str, Backup]:
        if not self._site.backups.directory.is_dir():
            return {}

        files_by_timestamp: dict[str, list[BackupFile]] = {}
        for path in self._site.backups.directory.iterdir():
            if path.is_file():
                backup_file = self.get_local_file(path)
                files_by_timestamp.setdefault(backup_file.timestamp, []).append(backup_file)

        backups = {}
        for timestamp, files in files_by_timestamp.items():
            files = sorted(files, key=lambda f: f.kind)
            created_at = files[0].created_at
            backups[timestamp] = Backup(timestamp=timestamp, created_at=created_at, files=files)
        return backups

    def get_offsite_backups(self, limit: int | None) -> dict[str, Backup]:
        if not self._bench.config.s3.is_configured:
            return {}

        offsite = OffsiteBackup.from_config(self._bench.config.s3, self._bench.path)
        backups = {}
        for timestamp, files_by_type in offsite.list_backups(self._site_name, limit=limit).items():
            files = [
                self.get_offsite_file(timestamp, file_type, filename)
                for file_type, filename in files_by_type.items()
            ]
            created_at = self._get_timestamp_or_now(timestamp)
            backups[timestamp] = Backup(timestamp, created_at, files, is_offsite=True)
        return backups

    @staticmethod
    def merge_backups(
        local_backups: dict[str, Backup], offsite_backups: dict[str, Backup]
    ) -> dict[str, Backup]:
        """Union keyed by timestamp; where a backup happened both places, local
        files win and only the offsite kinds missing locally get added."""
        backups = dict(local_backups)
        for timestamp, offsite_backup in offsite_backups.items():
            local_backup = backups.get(timestamp)
            if local_backup is None:
                backups[timestamp] = offsite_backup
                continue
            local_backup.is_offsite = True
            local_kinds = {f.kind for f in local_backup.files}
            local_backup.files.extend(f for f in offsite_backup.files if f.kind not in local_kinds)
        return backups

    def get_local_file(self, path: Path) -> BackupFile:
        stat = path.stat()
        name = path.name
        timestamp = parse_backup_timestamp(name) or "unknown"
        default_created_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        created_at = self.get_timestamp(timestamp) or default_created_at

        return BackupFile(
            filename=name,
            path=str(path),
            size_bytes=stat.st_size,
            created_at=created_at,
            kind=self.get_file_kind(name),
            timestamp=timestamp,
        )

    def get_offsite_file(self, timestamp: str, file_type: str, filename: str) -> BackupFile:
        return BackupFile(
            filename=filename,
            path="",
            size_bytes=0,
            created_at=self._get_timestamp_or_now(timestamp),
            kind=_OFFSITE_FILE_KINDS.get(file_type, "site_config"),
            timestamp=timestamp,
        )

    @staticmethod
    def get_file_kind(filename: str) -> str:
        if "private-files" in filename:
            return "private-file"
        if "files" in filename:
            return "public-file"
        if "database" in filename:
            return "database"
        return "site_config"

    @staticmethod
    def get_timestamp(timestamp: str) -> datetime | None:
        try:
            return datetime.strptime(timestamp, "%Y%m%d_%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            return None

    @classmethod
    def _get_timestamp_or_now(cls, timestamp: str) -> datetime:
        return cls.get_timestamp(timestamp) or datetime.now(UTC)
