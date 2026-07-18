"""Offsite backup storage: uploads/downloads/deletes a bench's site backups in S3."""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from pilot.config import S3Config
from pilot.integrations.s3.base import S3
from pilot.internal.atomic_file import exclusive_file_lock

FILE_TYPE_SUFFIXES = {
    "-database.sql.gz": "database",
    "-private-files.tar": "private_files",
    "-files.tar": "files",
    "-site_config_backup.json": "site_config",
}


def _file_type(filename: str) -> str:
    for suffix, file_type in FILE_TYPE_SUFFIXES.items():
        if filename.endswith(suffix):
            return file_type
    return "unknown"


@dataclass(frozen=True)
class BackupKeys:
    """Builds S3 keys for backup files and monthly metadata."""

    site_name: str

    def get_file_key(self, timestamp: str, filename: str) -> str:
        date, time = timestamp.split("_")
        return f"sites/{self.site_name}/backups/{date}/{time}/{filename}"

    def get_month_key(self, timestamp: str) -> str:
        date = timestamp.split("_")[0]
        return f"sites/{self.site_name}/backups_metadata/{date[:4]}-{date[4:6]}.json"

    @property
    def month_prefix(self) -> str:
        return f"sites/{self.site_name}/backups_metadata/"


class Metadata:
    """Monthly offsite-backup index, locked during read-modify-write."""

    def __init__(self, s3: S3, bucket: str, keys: BackupKeys, lock: Path):
        self.s3 = s3
        self.bucket = bucket
        self.keys = keys
        self.lock = lock

    def add(self, timestamp: str, filename: str) -> None:
        key = self.keys.get_month_key(timestamp)
        with exclusive_file_lock(self.lock):
            runs = self._read_month(key)
            runs.setdefault(timestamp, {})[_file_type(filename)] = filename
            self.s3.write_json(self.bucket, key, runs)

    def remove(self, timestamp: str, filename: str) -> None:
        key = self.keys.get_month_key(timestamp)
        with exclusive_file_lock(self.lock):
            runs = self._read_month(key)
            run = runs.get(timestamp)
            if run is None:
                return
            run.pop(_file_type(filename), None)
            if not run:
                runs.pop(timestamp)
            self.s3.write_json(self.bucket, key, runs)

    def iter_runs(self) -> Iterator[tuple[str, dict[str, str]]]:
        """Yield (timestamp, files) pairs newest first, one month at a time."""
        month_keys = self.s3.list_objects(self.bucket, prefix=self.keys.month_prefix)
        for key in sorted(month_keys, reverse=True):
            runs = self.s3.read_json(self.bucket, key)
            for timestamp in sorted(runs, reverse=True):
                yield timestamp, runs[timestamp]

    def _read_month(self, key: str) -> dict[str, dict[str, str]]:
        if not self.s3.has_object(self.bucket, key):
            return {}
        return self.s3.read_json(self.bucket, key)


class OffsiteBackup:
    """Uploads, downloads and deletes site backups in one bench bucket."""

    def __init__(self, s3: S3, bucket: str, bench_root: Path) -> None:
        self.s3 = s3
        self.bucket = bucket
        self.bench_root = Path(bench_root)

    @classmethod
    def from_config(cls, config: S3Config, bench_root: Path) -> "OffsiteBackup":
        """Connect using bench.toml's [s3] section, creating the bucket on first use."""
        client = S3.from_config(config)
        return cls(client, config.bucket, bench_root)

    def upload(self, site_name: str, timestamp: str, backup_path: Path, remove_local: bool = True) -> None:
        keys = BackupKeys(site_name)
        self.s3.upload_file(self.bucket, backup_path, keys.get_file_key(timestamp, backup_path.name))
        self._metadata(keys).add(timestamp, backup_path.name)
        if remove_local:
            backup_path.unlink(missing_ok=True)

    def download(self, site_name: str, timestamp: str, filename: str, destination: Path) -> None:
        self.s3.download_file(self.bucket, BackupKeys(site_name).get_file_key(timestamp, filename), destination)

    def presigned_url(self, site_name: str, timestamp: str, filename: str, expires_in: int = 25_000) -> str:
        """A direct, time-limited S3 download link - the file goes straight
        from S3 to whoever has the link, without passing through this server."""
        key = BackupKeys(site_name).get_file_key(timestamp, filename)
        return self.s3.presigned_url(self.bucket, key, expires_in=expires_in)

    def delete(self, site_name: str, timestamp: str, filename: str) -> None:
        keys = BackupKeys(site_name)
        self.s3.delete_object(self.bucket, keys.get_file_key(timestamp, filename))
        self._metadata(keys).remove(timestamp, filename)

    def list_backups(self, site_name: str, limit: int | None = None) -> dict[str, dict[str, str]]:
        """Return offsite backup runs newest first, keyed by timestamp."""
        runs: dict[str, dict[str, str]] = {}
        for timestamp, files in self._metadata(BackupKeys(site_name)).iter_runs():
            runs[timestamp] = files
            if limit is not None and len(runs) >= limit:
                break
        return runs

    def get_backup(self, site_name: str, timestamp: str) -> dict[str, str] | None:
        """Return one offsite backup run from its monthly metadata file."""
        keys = BackupKeys(site_name)
        return self._metadata(keys)._read_month(keys.get_month_key(timestamp)).get(timestamp)

    def _metadata(self, keys: BackupKeys) -> Metadata:
        return Metadata(self.s3, self.bucket, keys, self.bench_root / ".backup-metadata")
