from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command


@dataclass(kw_only=True)
class SetupRemoteDatabaseCommand(Command):
    """Move this host's benches to a MariaDB on another host."""

    name: ClassVar[str] = "remote-database"
    help: ClassVar[str] = "Manage a MariaDB on another host over SSH and remove the local one."
    group: ClassVar[str] = "setup"

    host: Annotated[str, Arg(help="Address of the database host, e.g. its private IPv6 address.")]
    ssh_user: Annotated[str, Arg(help="User that logs in to the database host with passwordless sudo.")]
    ssh_key: Annotated[str, Arg(help="Private key for that login.")]
    memory_mb: Annotated[int, Arg(help="Memory of the database host in MiB, from its VM spec.")]
    port: Annotated[int, Arg(help="Port the remote MariaDB listens on.")] = 3306
    admin_user: Annotated[str, Arg(help="Admin account Pilot creates on the remote server.")] = "pilot"

    def run(self) -> None:
        """Set up the remote database server."""
        self.bench.setup_remote_database(
            host=self.host,
            ssh_user=self.ssh_user,
            ssh_key_path=self.ssh_key,
            memory_mb=self.memory_mb,
            port=self.port,
            admin_user=self.admin_user,
            on_progress=self.report,
        )
        self.report(f"This host now uses the MariaDB server on {self.host}.")
