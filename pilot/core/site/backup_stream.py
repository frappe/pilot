import contextlib
import os
import tempfile
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from pilot.exceptions import BenchError
from pilot.integrations.s3.backups import OffsiteBackup

if TYPE_CHECKING:
    from pilot.core.site import Site

BACKUP_PATH_FLAGS = {
    "--backup-path-conf": "site_config_backup.json",
    "--backup-path-db": "database.sql.gz",
    "--backup-path-files": "files.tar",
    "--backup-path-private-files": "private-files.tar",
}


class IncompleteBackupError(BenchError):
    pass


class FifoReader:
    """Reads one backup FIFO for boto3. EOF before `is_complete` raises, so a cut-short
    backup aborts its multipart upload instead of finishing a truncated object."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.size = 0
        self.is_complete = False
        self.file: BinaryIO

    def read(self, size: int = -1) -> bytes:
        data = self.file.read(size)
        self.size += len(data)
        if not data and not self.is_complete:
            raise IncompleteBackupError(f"The backup stopped before {self.path.name} was complete.")
        return data


class SiteBackupStream:
    """Pipes `frappe backup` through FIFOs straight into S3, writing nothing to disk."""

    def __init__(self, site: "Site", offsite: OffsiteBackup) -> None:
        self.site = site
        self.offsite = offsite

    def run(self, with_files: bool) -> tuple[str, dict[str, int]]:
        """Return the run's timestamp and the uploaded size of each file."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        slug = self.site.config.name.replace(".", "_")
        flags = list(BACKUP_PATH_FLAGS)[: 4 if with_files else 2]
        self.site.backups.directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".stream-", dir=self.site.backups.directory) as staging:
            readers = {
                flag: FifoReader(Path(staging) / f"{timestamp}-{slug}-{BACKUP_PATH_FLAGS[flag]}")
                for flag in flags
            }
            for reader in readers.values():
                os.mkfifo(reader.path, 0o600)
            with ThreadPoolExecutor(max_workers=len(readers)) as pool:
                uploads = {reader: pool.submit(self.upload, timestamp, reader) for reader in readers.values()}
                try:
                    self.run_backup(readers)
                finally:
                    self.check_uploads(timestamp, uploads)
        return timestamp, {reader.path.name: reader.size for reader in readers.values()}

    def upload(self, timestamp: str, reader: FifoReader) -> None:
        """A failed upload drains its FIFO, so the backup never blocks writing to it."""
        with open(reader.path, "rb") as reader.file:
            try:
                self.offsite.upload_stream(self.site.config.name, timestamp, reader.path.name, reader)
            except Exception:
                while reader.file.read(1024 * 1024):
                    pass
                raise

    def run_backup(self, readers: dict[str, FifoReader]) -> None:
        """Frappe closes the database file between its header and the dump, so pilot holds
        a writer on every FIFO until the backup exits; readers see one EOF at the end."""
        from pilot.utils import run_command

        argv = [*self.site.bench.frappe_call, "frappe", "--site", self.site.config.name, "backup"]
        if "--backup-path-files" in readers:
            argv.append("--with-files")
        for flag, reader in readers.items():
            argv += [flag, str(reader.path)]
        holders = []
        try:
            for reader in readers.values():
                holders.append(os.open(reader.path, os.O_WRONLY))
            run_command(argv, cwd=self.site.bench.sites_path, stream_output=True)
            for reader in readers.values():
                reader.is_complete = True
        finally:
            unopened = list(readers.values())[len(holders) :]
            for holder in holders:
                os.close(holder)
            # A reader with no writer waits in open() for ever; a brief writer releases it.
            for reader in unopened:
                with contextlib.suppress(OSError):
                    os.close(os.open(reader.path, os.O_WRONLY))

    def check_uploads(self, timestamp: str, uploads: dict[FifoReader, Future]) -> None:
        """Raise the first upload error. Incomplete reads only echo the backup's own failure.
        Files that did upload are deleted, so a failed run leaves nothing offsite."""
        results = {reader: future.exception() for reader, future in uploads.items()}
        errors = [
            error for error in results.values() if error and not isinstance(error, IncompleteBackupError)
        ]
        if not errors:
            return
        for reader, error in results.items():
            if error is not None:
                continue
            try:
                self.offsite.delete(self.site.config.name, timestamp, reader.path.name)
            except Exception as cleanup_error:
                errors[0].add_note(f"Could not delete {reader.path.name} from S3: {cleanup_error}")
        raise errors[0]
