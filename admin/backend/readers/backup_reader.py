from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pilot.config.toml_store import BenchTomlStore
from pilot.core.bench import Bench

_TS_RE = re.compile(r"^(\d{8}_\d{6})")

_REMOTE_FILE_KINDS = {
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
    kind: str  # 'database' | 'public-file' | 'private-file' | 'site_config'
    timestamp: str


@dataclass
class BackupSet:
    timestamp: str
    created_at: datetime
    files: list[BackupFile]
    is_offsite: bool = False


class BackupReader:
    def __init__(self, bench_root: Path, site_name: str) -> None:
        self.site_name = site_name
        self._backups_dir = bench_root / "sites" / site_name / "private" / "backups"
        self.bench = Bench(BenchTomlStore.for_bench(bench_root).read(), bench_root)

    def read_all(self, limit: int | None = None) -> list[BackupSet]:
        """Backup sets newest first. When `limit` is given, only that many
        offsite monthly metadata files are fetched from S3 (see
        `_read_remote_backups`), and the merged, sorted result is truncated to
        `limit` — so a paginated caller never pays for a site's full history."""
        merged = self._merge(self._read_local_backups(), self._read_remote_backups(limit))
        return merged[:limit] if limit is not None else merged

    def _merge(self, local: list[BackupSet], remote: list[BackupSet]) -> list[BackupSet]:
        # Single pass: remote first so local (appended after, and read fresh
        # off disk) wins when both sides have files for the same kind/timestamp.
        files_by_ts: dict[str, dict[str, BackupFile]] = {}
        offsite_timestamps: set[str] = set()
        for backup_set in remote:
            offsite_timestamps.add(backup_set.timestamp)
            files_by_ts.setdefault(backup_set.timestamp, {}).update({f.kind: f for f in backup_set.files})
        for backup_set in local:
            files_by_ts.setdefault(backup_set.timestamp, {}).update({f.kind: f for f in backup_set.files})

        result = [
            BackupSet(
                timestamp=ts,
                created_at=next(iter(files.values())).created_at,
                files=list(files.values()),
                is_offsite=ts in offsite_timestamps,
            )
            for ts, files in files_by_ts.items()
        ]
        return sorted(result, key=lambda backup_set: backup_set.timestamp, reverse=True)

    def _read_remote_backups(self, limit: int | None = None) -> list[BackupSet]:
        if not self.bench.is_s3_configured:
            return []

        offsite_backup = self.bench.offsite_backup()
        result = []
        for timestamp, files_by_type in offsite_backup.list_backups(self.site_name, limit=limit).items():
            files = [self._remote_file(timestamp, file_type, filename) for file_type, filename in files_by_type.items()]
            result.append(
                BackupSet(
                    timestamp=timestamp,
                    created_at=self._parse_timestamp(timestamp),
                    files=files,
                )
            )
        return result

    def _remote_file(self, timestamp: str, file_type: str, filename: str) -> BackupFile:
        return BackupFile(
            filename=filename,
            path="",
            size_bytes=0,
            created_at=self._parse_timestamp(timestamp),
            kind=_REMOTE_FILE_KINDS.get(file_type, "site_config"),
            timestamp=timestamp,
        )

    def _parse_timestamp(self, timestamp: str) -> datetime:
        try:
            return datetime.strptime(timestamp, "%Y%m%d_%H%M%S")
        except ValueError:
            return datetime.now()

    def _read_local_backups(self) -> list[BackupSet]:
        if not self._backups_dir.is_dir():
            return []

        by_ts: dict[str, list[BackupFile]] = {}
        for f in self._backups_dir.iterdir():
            if not f.is_file():
                continue
            bf = self._parse_file(f)
            by_ts.setdefault(bf.timestamp, []).append(bf)

        result = []
        for ts in sorted(by_ts, reverse=True):
            files = sorted(by_ts[ts], key=lambda f: f.kind)
            result.append(BackupSet(timestamp=ts, created_at=files[0].created_at, files=files))
        return result

    def _parse_file(self, path: Path) -> BackupFile:
        name = path.name
        m = _TS_RE.match(name)
        ts = m.group(1) if m else "unknown"

        try:
            created_at = datetime.strptime(ts, "%Y%m%d_%H%M%S")
        except ValueError:
            created_at = datetime.fromtimestamp(path.stat().st_mtime)

        if "private-files" in name:
            kind = "private-file"
        elif "files" in name:
            kind = "public-file"
        elif "database" in name:
            kind = "database"
        else:
            kind = "site_config"

        return BackupFile(filename=name, path=str(path), size_bytes=path.stat().st_size, created_at=created_at, kind=kind, timestamp=ts)
