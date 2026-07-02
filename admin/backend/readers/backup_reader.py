from __future__ import annotations

import gzip
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_TS_RE = re.compile(r"^(\d{8}_\d{6})")

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


class BackupReader:
    def __init__(self, bench_root: Path, site_name: str) -> None:
        self._backups_dir = bench_root / "sites" / site_name / "private" / "backups"

    def read_all(self) -> list[BackupSet]:
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
