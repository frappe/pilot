from __future__ import annotations

import fcntl
from pathlib import Path
from typing import IO

from pilot.secure_files import open_private


class WorkerLock:
    def __init__(self, handle: IO) -> None:
        self._handle = handle

    @classmethod
    def try_acquire(cls, path: Path) -> WorkerLock | None:
        handle = open_private(path, "a")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return None
        return cls(handle)

    def release(self) -> None:
        if self._handle.closed:
            return
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()

    def __enter__(self) -> WorkerLock:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()
