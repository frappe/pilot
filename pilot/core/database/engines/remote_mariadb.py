from __future__ import annotations

from pathlib import Path

from pilot.core.database.engines.mariadb import MariaDB
from pilot.core.database.log_file import LogFile, RemoteLogFile
from pilot.exceptions import CommandError
from pilot.managers.ssh_host import SSHHost


class RemoteMariaDB(MariaDB):
    """A MariaDB on another host. SQL goes over TCP; its logs and disk are read over SSH."""

    def __init__(self, *args, ssh_host: SSHHost, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.ssh_host = ssh_host

    def get_log_file(self, variable: str) -> LogFile | None:
        """The file a log variable names on the database host, or None when it is unset or missing."""
        path = self.get_variable_path(variable)
        if path is None or not self.ssh_host.exists(str(path)):
            return None
        return self.open_server_file(path)

    def open_server_file(self, path: Path) -> LogFile:
        """A file on the database host."""
        return RemoteLogFile(path, self.ssh_host)

    def get_free_disk_bytes(self) -> int | None:
        """Free space on the database host's disk that holds the data directory."""
        try:
            return self.ssh_host.get_free_disk_bytes(str(self.get_scalar("SELECT @@datadir")))
        except (CommandError, ValueError, IndexError):
            return None
