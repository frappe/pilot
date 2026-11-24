from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import DatabaseError

if TYPE_CHECKING:
    from pilot.core.database.agent.client import DatabaseAgentClient


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
    """A database server file on the database VM, read through the database agent."""

    def __init__(self, path: Path, agent: DatabaseAgentClient) -> None:
        super().__init__(path)
        self.agent = agent

    @property
    def exists(self) -> bool:
        """Whether the file exists on the database VM."""
        return self.agent.get_file(str(self.path))["exists"]

    def read_tail(self, size: int) -> str:
        """The last `size` bytes as text."""
        return self.agent.get_file(str(self.path), tail_bytes=size)["text"]

    @property
    def size_bytes(self) -> int:
        """The file size, or 0 when it cannot be read."""
        try:
            return self.agent.get_file(str(self.path))["size_bytes"]
        except DatabaseError:
            return 0
