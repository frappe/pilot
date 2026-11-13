from __future__ import annotations

import os
from pathlib import Path
from typing import IO


class PrivateFile:
    """A file only its owner can read (mode 0600), opened without following a symbolic link."""

    mode = 0o600

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def open(self, mode: str = "w", *, exclusive: bool = False) -> IO:
        """Open for writing ("w", "wb") or appending ("a", "ab"); exclusive fails if it exists."""
        if mode not in {"w", "wb", "a", "ab"}:
            raise ValueError(f"Unsupported private file mode: {mode!r}")
        flags = os.O_WRONLY | os.O_CREAT
        flags |= os.O_APPEND if mode.startswith("a") else os.O_TRUNC
        if exclusive:
            flags |= os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.path, flags, self.mode)
        try:
            os.fchmod(descriptor, self.mode)
            return os.fdopen(descriptor, mode)
        except Exception:
            os.close(descriptor)
            raise

    def write_text(self, content: str) -> None:
        """Replace the file's content."""
        with self.open() as handle:
            handle.write(content)


class PrivateDirectory:
    """A directory only its owner can enter (mode 0700)."""

    mode = 0o700

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def create(self, *, parents: bool = False) -> None:
        """Create it if missing and tighten its mode; refuse a symbolic link."""
        self.path.mkdir(mode=self.mode, parents=parents, exist_ok=True)
        if self.path.is_symlink():
            raise OSError(f"Refusing to secure a symbolic-link directory: {self.path}")
        self.path.chmod(self.mode)
