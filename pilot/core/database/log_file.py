from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import CommandError

if TYPE_CHECKING:
    from pilot.managers.ssh_host import SSHHost


class LogFile:
    """A database server file on this host, read from its end."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def read_tail(self, size: int) -> str:
        """The last `size` bytes as text."""
        with self.path.open("rb") as log:
            end = log.seek(0, 2)
            log.seek(max(0, end - size))
            return log.read().decode("utf-8", errors="replace")

    @property
    def size_bytes(self) -> int:
        """The file size, or 0 when it cannot be read."""
        try:
            return self.path.stat().st_size
        except OSError:
            return 0


class RemoteLogFile(LogFile):
    """A database server file on the database host, read over SSH."""

    def __init__(self, path: Path, ssh_host: SSHHost) -> None:
        super().__init__(path)
        self.ssh_host = ssh_host

    def read_tail(self, size: int) -> str:
        """The last `size` bytes as text."""
        return self.ssh_host.run(["tail", "-c", str(size), str(self.path)])

    @property
    def size_bytes(self) -> int:
        """The file size, or 0 when it cannot be read."""
        try:
            return int(self.ssh_host.run(["stat", "-c", "%s", str(self.path)]).strip())
        except (CommandError, ValueError):
            return 0
