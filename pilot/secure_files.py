from __future__ import annotations

import os
from pathlib import Path
from typing import IO

PRIVATE_FILE_MODE = 0o600
PRIVATE_DIRECTORY_MODE = 0o700


def open_private(path: Path, mode: str = "w", *, exclusive: bool = False) -> IO:
    """Open a private file without exposing newly written content to other users."""
    path = Path(path)
    if mode not in {"w", "wb", "a", "ab"}:
        raise ValueError(f"Unsupported private file mode: {mode!r}")

    flags = os.O_WRONLY | os.O_CREAT
    flags |= os.O_APPEND if mode.startswith("a") else os.O_TRUNC
    if exclusive:
        flags |= os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    fd = os.open(path, flags, PRIVATE_FILE_MODE)
    try:
        os.fchmod(fd, PRIVATE_FILE_MODE)
        return os.fdopen(fd, mode)
    except Exception:
        os.close(fd)
        raise


def write_private_text(path: Path, content: str) -> None:
    with open_private(path) as handle:
        handle.write(content)


def make_private_directory(path: Path, *, parents: bool = False) -> None:
    path = Path(path)
    path.mkdir(mode=PRIVATE_DIRECTORY_MODE, parents=parents, exist_ok=True)
    if path.is_symlink():
        raise OSError(f"Refusing to secure a symbolic-link directory: {path}")
    path.chmod(PRIVATE_DIRECTORY_MODE)
