"""Offsite backup storage: uploads/downloads/deletes a bench's site backups in S3."""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from pilot.config.s3_config import S3Config
from pilot.integrations.s3.base import S3

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
    """Every S3 key the backups feature touches, built in one place.

    Fixed layout::

        sites/<site>/backups/<date>/<time>/<filename>       backup files
        sites/<site>/backups_metadata/<year>-<month>.json    monthly run index
    """

    site_name: str

    def file(self, timestamp: str, filename: str) -> str:
        date, time = timestamp.split("_")
        return f"sites/{self.site_name}/backups/{date}/{time}/{filename}"

    def month(self, timestamp: str) -> str:
        date = timestamp.split("_")[0]
        return f"sites/{self.site_name}/backups_metadata/{date[:4]}-{date[4:6]}.json"

    @property
    def month_prefix(self) -> str:
        return f"sites/{self.site_name}/backups_metadata/"


class Metadata:
    """Monthly index of one site's offsite backup runs.

    Each monthly file groups backup runs by timestamp, so a run's files
    (database, site files, private files, site config) render as one row::

        {
          "20260702_174545": {
            "database": "20260702_174545-assets_local-database.sql.gz",
            "files": "20260702_174545-assets_local-files.tar",
            "private_files": "20260702_174545-assets_local-private-files.tar",
            "site_config": "20260702_174545-assets_local-site_config_backup.json"
          }
        }
    """

    def __init__(self, s3: S3, bucket: str, keys: BackupKeys):
        self.s3 = s3
        self.bucket = bucket
        self.keys = keys

    def add(self, timestamp: str, filename: str) -> None:
        key = self.keys.month(timestamp)
        runs = self._read_month(key)
        runs.setdefault(timestamp, {})[_file_type(filename)] = filename
        self.s3.write_json(self.bucket, key, runs)

    def remove(self, timestamp: str, filename: str) -> None:
        key = self.keys.month(timestamp)
        runs = self._read_month(key)
        run = runs.get(timestamp)
        if run is None:
            return
        run.pop(_file_type(filename), None)
        if not run:
            runs.pop(timestamp)
        self.s3.write_json(self.bucket, key, runs)

    def iter_runs(self) -> Iterator[tuple[str, dict[str, str]]]:
        """(timestamp, files) pairs across every monthly file, newest first.
        Fetches one month at a time so a caller that only needs the most recent
        runs can stop early instead of paying for the whole history."""
        month_keys = self.s3.list_objects(self.bucket, prefix=self.keys.month_prefix)
        for key in sorted(month_keys, reverse=True):
            runs = self.s3.read_json(self.bucket, key)
            for timestamp in sorted(runs, reverse=True):
                yield timestamp, runs[timestamp]

    def _read_month(self, key: str) -> dict[str, dict[str, str]]:
        if not self.s3.object_exists(self.bucket, key):
            return {}
        return self.s3.read_json(self.bucket, key)


class OffsiteBackup:
    """Uploads, downloads and deletes site backups in one bench's bucket.

    Wraps a configured ``S3`` client (composition, not inheritance: this class
    is not itself an S3 client), with all key naming delegated to ``BackupKeys``
    and run bookkeeping to ``Metadata``.
    """

    def __init__(self, s3: S3, bucket: str) -> None:
        self.s3 = s3
        self.bucket = bucket

    @classmethod
    def from_config(cls, config: S3Config) -> "OffsiteBackup":
        """Connect using bench.toml's [s3] section, creating the bucket on first use."""
        client = S3.from_config(config)
        return cls(client, config.bucket)

    def upload(self, site_name: str, timestamp: str, backup_path: Path, remove_local: bool = True) -> None:
        keys = BackupKeys(site_name)
        self.s3.upload_file(self.bucket, backup_path, keys.file(timestamp, backup_path.name))
        self._metadata(keys).add(timestamp, backup_path.name)
        if remove_local:
            backup_path.unlink(missing_ok=True)

    def download(self, site_name: str, timestamp: str, filename: str, destination: Path) -> None:
        self.s3.download_file(self.bucket, BackupKeys(site_name).file(timestamp, filename), destination)

    def presigned_url(self, site_name: str, timestamp: str, filename: str, expires_in: int = 25_000) -> str:
        """A direct, time-limited S3 download link — the file goes straight
        from S3 to whoever has the link, without passing through this server."""
        key = BackupKeys(site_name).file(timestamp, filename)
        return self.s3.presigned_url(self.bucket, key, expires_in=expires_in)

    def delete(self, site_name: str, timestamp: str, filename: str) -> None:
        keys = BackupKeys(site_name)
        self.s3.delete_object(self.bucket, keys.file(timestamp, filename))
        self._metadata(keys).remove(timestamp, filename)

    def list_backups(self, site_name: str, limit: int | None = None) -> dict[str, dict[str, str]]:
        """Offsite backup runs for a site, newest first, keyed by timestamp.
        Stops reading monthly metadata files as soon as `limit` runs are
        collected, instead of fetching a site's entire backup history."""
        runs: dict[str, dict[str, str]] = {}
        for timestamp, files in self._metadata(BackupKeys(site_name)).iter_runs():
            runs[timestamp] = files
            if limit is not None and len(runs) >= limit:
                break
        return runs

    def get_backup(self, site_name: str, timestamp: str) -> dict[str, str] | None:
        """Files for a single backup run, or None if it doesn't exist offsite.
        Reads only that run's monthly metadata file, not the whole history."""
        keys = BackupKeys(site_name)
        return self._metadata(keys)._read_month(keys.month(timestamp)).get(timestamp)

    def _metadata(self, keys: BackupKeys) -> Metadata:
        return Metadata(self.s3, self.bucket, keys)
