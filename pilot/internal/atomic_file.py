from __future__ import annotations

import ctypes
import errno
import fcntl
import os
import stat
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from pilot.utils import PRIVATE_FILE_MODE, open_private


def exchange_paths(first: Path, second: Path) -> None:
    """Atomically swap two directory entries without following symbolic links."""
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        exchange = library.renamex_np
        exchange.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        arguments = [os.fsencode(first), os.fsencode(second), 2]  # RENAME_SWAP
    else:
        exchange = getattr(library, "renameat2", None)
        if exchange is None:
            raise OSError(errno.ENOTSUP, "Atomic directory exchange is unavailable")
        exchange.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        arguments = [-100, os.fsencode(first), -100, os.fsencode(second), 2]  # AT_FDCWD, RENAME_EXCHANGE
    exchange.restype = ctypes.c_int
    if exchange(*arguments) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(first))


def _lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")


@contextmanager
def exclusive_file_lock(path: Path, *, blocking: bool = True) -> Iterator[None]:
    """Hold a process-wide advisory lock associated with ``path``."""
    with open_private(_lock_path(path), mode="a") as lock_file:
        existing = _existing_file_metadata(path)
        lock_metadata = os.fstat(lock_file.fileno())
        if existing is not None and lock_metadata.st_uid != existing.st_uid:
            os.fchown(lock_file.fileno(), existing.st_uid, existing.st_gid)
        operation = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(lock_file.fileno(), operation)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def atomic_write_private_text(path: Path, content: str) -> None:
    """Durably replace a file while serializing writers through a private lock."""
    path = Path(path)
    with exclusive_file_lock(path):
        replace_private_text_locked(path, content)


def replace_private_text_locked(path: Path, content: str) -> None:
    """Replace ``path`` atomically while its ``exclusive_file_lock`` is held."""
    path = Path(path)
    existing = _existing_file_metadata(path)
    temporary_path: Path | None = None

    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as temporary_file:
            os.fchmod(temporary_file.fileno(), PRIVATE_FILE_MODE)
            _preserve_owner(temporary_file.fileno(), existing)
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        _verify_existing_file(path, existing)
        os.replace(temporary_path, path)
        temporary_path = None
        _fsync_directory(path.parent)
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)


def _existing_file_metadata(path: Path) -> os.stat_result | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(metadata.st_mode):
        raise OSError(f"Refusing to replace symbolic link: {path}")
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"Refusing to replace non-regular file: {path}")
    return metadata


def _preserve_owner(descriptor: int, existing: os.stat_result | None) -> None:
    if existing is None:
        return
    temporary = os.fstat(descriptor)
    if (temporary.st_uid, temporary.st_gid) != (existing.st_uid, existing.st_gid):
        os.fchown(descriptor, existing.st_uid, existing.st_gid)


def _verify_existing_file(path: Path, expected: os.stat_result | None) -> None:
    current = _existing_file_metadata(path)
    if expected is None and current is None:
        return
    if expected is None or current is None:
        raise OSError(f"File changed during atomic write: {path}")
    if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        raise OSError(f"File changed during atomic write: {path}")


def fsync_directory_files(path: Path) -> None:
    """Flush every file in a directory, then the directory entries, to disk."""
    for child in Path(path).iterdir():
        if child.is_file() and not child.is_symlink():
            with child.open("rb") as file:
                os.fsync(file.fileno())
    _fsync_directory(path)


def _fsync_directory(path: Path) -> None:
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_descriptor = os.open(path, directory_flags)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
