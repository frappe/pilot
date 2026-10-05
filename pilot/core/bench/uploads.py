from __future__ import annotations

import contextlib
import json
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import IO, TYPE_CHECKING

from pilot.exceptions import BenchError, UploadOffsetError
from pilot.internal.atomic_file import atomic_write_private_text, exclusive_file_lock
from pilot.utils import make_private_directory, open_private

if TYPE_CHECKING:
    from pilot.core.bench import Bench

CHUNK_SIZE = 256 * 1024 * 1024
STALE_SECONDS = 3 * 60 * 60
MANIFEST_NAME = "upload.json"
_COPY_BUFFER_SIZE = 1024 * 1024
_UPLOAD_ID = re.compile(r"^[0-9a-f]{16}$")
# Saved under names Frappe's backup files carry, so the restore can tell the parts apart.
_FILE_NAMES = {
    "database": ("upload-database.sql", "upload-database.sql.gz"),
    "public": ("upload-files.tar", "upload-files.tgz"),
    "private": ("upload-private-files.tar", "upload-private-files.tgz"),
    "config": ("upload-site_config_backup.json", "upload-site_config_backup.json"),
}


def upload_file_name(part: str, filename: str) -> str:
    plain, compressed = _FILE_NAMES[part]
    return compressed if filename.endswith((".gz", ".tgz")) else plain


class BackupUpload:
    """Backup files a browser sends in chunks. While the manifest exists the upload is in
    progress; a restore removes it and then owns the directory and its cleanup."""

    def __init__(self, bench: Bench, upload_id: str) -> None:
        if not _UPLOAD_ID.fullmatch(upload_id):
            raise BenchError("The upload does not exist.")
        self.bench = bench
        self.upload_id = upload_id

    @classmethod
    def start(cls, bench: Bench, site: str, files: dict[str, dict]) -> BackupUpload:
        """`files` maps each part to its `filename` and `size` in bytes."""
        cls.remove_stale(bench)
        make_private_directory(bench.uploads_path, parents=True)
        if shutil.disk_usage(bench.uploads_path).free < sum(file["size"] for file in files.values()):
            raise BenchError("The server does not have enough free disk space for these files.")

        upload = cls(bench, secrets.token_hex(8))
        make_private_directory(upload.path)
        names = {part: upload_file_name(part, file["filename"]) for part, file in files.items()}
        manifest = {"site": site, "files": {part: {"name": names[part], "size": file["size"]} for part, file in files.items()}}
        atomic_write_private_text(upload.manifest_path, json.dumps(manifest))
        return upload

    @classmethod
    def remove_stale(cls, bench: Bench) -> None:
        """Remove uploads that got no chunk for STALE_SECONDS. Claimed ones belong to a restore."""
        if not bench.uploads_path.is_dir():
            return
        cutoff = time.time() - STALE_SECONDS
        for directory in bench.uploads_path.iterdir():
            if (directory / MANIFEST_NAME).exists() and _last_modified(directory) < cutoff:
                shutil.rmtree(directory, ignore_errors=True)

    @property
    def path(self) -> Path:
        return self.bench.uploads_path / self.upload_id

    @property
    def manifest_path(self) -> Path:
        return self.path / MANIFEST_NAME

    @property
    def manifest(self) -> dict:
        if not self.manifest_path.exists():
            raise BenchError("The upload does not exist.")
        return json.loads(self.manifest_path.read_text())

    @property
    def site(self) -> str:
        return self.manifest["site"]

    @property
    def files(self) -> dict[str, dict]:
        """Each part with its expected `size` and the bytes `received`."""
        return {
            part: {"size": file["size"], "received": _size(self.path / file["name"])}
            for part, file in self.manifest["files"].items()
        }

    @property
    def is_complete(self) -> bool:
        return all(file["received"] == file["size"] for file in self.files.values())

    def write_chunk(self, part: str, offset: int, stream: IO[bytes], length: int) -> int:
        """Write a chunk at `offset` and return the bytes received. A chunk sent again
        rewrites from its offset, so retrying a failed chunk is safe."""
        file = self.manifest["files"].get(part)
        if file is None:
            raise BenchError(f"The upload has no {part} file.")
        if length > CHUNK_SIZE or offset < 0 or offset + length > file["size"]:
            raise BenchError("The chunk does not fit the file that was announced.")

        path = self.path / file["name"]
        with exclusive_file_lock(path):
            if not self.manifest_path.exists():
                raise BenchError("The upload was already handed to a restore.")
            received = _size(path)
            if offset > received:
                raise UploadOffsetError(received)
            if not path.exists():
                with open_private(path, "wb"):
                    pass
            with path.open("r+b") as target:
                target.seek(offset)
                target.truncate()
                copied = _copy(stream, target, length)
        if copied != length:
            # Keep the prefix that arrived; the client resumes after it.
            raise UploadOffsetError(offset + copied)
        return offset + length

    def claim(self, parts: list[str]) -> Path:
        """Hand the files to a restore. Without the manifest, cleanup ignores the directory."""
        files = self.manifest["files"]
        missing = [part for part in parts if part not in files]
        if missing:
            raise BenchError(f"The upload has no {' or '.join(missing)} file.")
        # Each file's lock, so a chunk being rewritten cannot change a file after the check.
        with contextlib.ExitStack() as locks:
            for file in files.values():
                locks.enter_context(exclusive_file_lock(self.path / file["name"]))
            if not self.is_complete:
                raise BenchError("Upload all the files before the restore.")
            self.manifest_path.unlink()
        return self.path

    def delete(self) -> None:
        """Cancel an upload in progress. A claimed upload belongs to its restore."""
        if self.manifest_path.exists():
            shutil.rmtree(self.path, ignore_errors=True)


def _copy(source: IO[bytes], target: IO[bytes], length: int) -> int:
    copied = 0
    while copied < length:
        block = source.read(min(_COPY_BUFFER_SIZE, length - copied))
        if not block:
            break
        target.write(block)
        copied += len(block)
    return copied


def _size(path: Path) -> int:
    return path.stat().st_size if path.exists() else 0


def _last_modified(directory: Path) -> float:
    return max((path.stat().st_mtime for path in directory.iterdir()), default=directory.stat().st_mtime)
