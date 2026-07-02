from __future__ import annotations

import gzip
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pilot.config.toml_store import BenchTomlStore
from pilot.core.bench import Bench
from pilot.integrations.s3.backups import OffsiteBackup

_TS_RE = re.compile(r"^(\d{8}_\d{6})")

# Metadata file_type -> the UI's file kind.
_REMOTE_FILE_KINDS = {
    "database": "database",
    "files": "public-file",
    "private_files": "private-file",
    "site_config": "site_config",
}

_MARIADB_MARKERS = ("MariaDB dump", "MySQL dump", "/*!40101", "CREATE TABLE `")
_POSTGRES_MARKERS = ("PostgreSQL database dump", 'CREATE TABLE "', "CREATE TABLE public.", "COPY public.")


def dump_engine(path: str | Path) -> str:
    """Return the engine ("mariadb" or "postgres") a SQL backup was produced by.

    Mirrors frappe.installer.get_dump_db_type: mysqldump quotes identifiers with
    backticks and emits `MariaDB dump`; pg_dump uses double quotes and `COPY public.`.
    """
    path = str(path)
    try:
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rb") as f:
            header = f.read(2048).decode(errors="ignore")
    except Exception:
        # Unreadable / corrupt / truncated upload: fall back so the caller returns a
        # clean error, not a 500 (zlib.error and EOFError from gzip aren't OSErrors).
        return "mariadb"
    if any(marker in header for marker in _MARIADB_MARKERS):
        return "mariadb"
    if any(marker in header for marker in _POSTGRES_MARKERS):
        return "postgres"
    return "mariadb" if "`" in header else "postgres"


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
        """Backup sets newest first: local runs, overlaid with offsite runs from
        S3. When `limit` is given, only that many offsite runs are fetched (the
        monthly metadata files are read lazily) and the result is truncated to
        `limit` — so a paginated caller never pays for a site's full history."""
        sets = {backup_set.timestamp: backup_set for backup_set in self._read_local_backups()}
        self._overlay_remote_backups(sets, limit)
        ordered = sorted(sets.values(), key=lambda backup_set: backup_set.timestamp, reverse=True)
        return ordered[:limit] if limit is not None else ordered

    def _overlay_remote_backups(self, sets: dict[str, BackupSet], limit: int | None) -> None:
        """Marks sets that exist offsite and adds remote-only runs/files.
        A file the site still has locally is left untouched — the local copy is
        authoritative (it has a real path and size); remote fills the gaps."""
        if not self.bench.config.s3.is_configured:
            return

        offsite_backup = OffsiteBackup.from_config(self.bench.config.s3)
        for timestamp, files_by_type in offsite_backup.list_backups(self.site_name, limit=limit).items():
            backup_set = sets.setdefault(timestamp, BackupSet(timestamp, self._parse_timestamp(timestamp), []))
            backup_set.is_offsite = True
            local_kinds = {file.kind for file in backup_set.files}
            for file_type, filename in files_by_type.items():
                file = self._remote_file(timestamp, file_type, filename)
                if file.kind not in local_kinds:
                    backup_set.files.append(file)
                    local_kinds.add(file.kind)

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
