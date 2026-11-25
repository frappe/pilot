from __future__ import annotations

from pathlib import Path

from pilot.core.agent.roles.database.client import DatabaseAgentClient
from pilot.core.database.engines.mariadb import MariaDB
from pilot.core.database.log_file import LogFile, RemoteLogFile
from pilot.exceptions import DatabaseError


class RemoteMariaDB(MariaDB):
    """A MariaDB on its own VM. SQL goes over TCP; its logs and disk are read through the Pilot agent."""

    def __init__(self, *args, agent: DatabaseAgentClient, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.agent = agent

    def get_log_file(self, variable: str) -> LogFile | None:
        """The file a log variable names on the database VM, or None when it is unset or missing."""
        path = self.get_variable_path(variable)
        if path is None or not (file := self.open_server_file(path)).exists:
            return None
        return file

    def open_server_file(self, path: Path) -> RemoteLogFile:
        """A file on the database VM."""
        return RemoteLogFile(path, self.agent)

    def get_free_disk_bytes(self) -> int | None:
        """Free space on the database VM's disk that holds the data directory."""
        try:
            return self.agent.get_free_disk_bytes()
        except DatabaseError:
            return None
