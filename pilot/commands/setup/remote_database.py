from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command
from pilot.exceptions import BenchError


@dataclass(kw_only=True)
class SetupRemoteDatabaseCommand(Command):
    """Move this host's benches to a MariaDB on another VM."""

    name: ClassVar[str] = "remote-database"
    help: ClassVar[str] = (
        "Manage the MariaDB on a database VM through its Pilot agent and remove the local one. "
        "--migrate copies the databases of existing sites there first. Reads the agent's token from the terminal or from standard input."
    )
    group: ClassVar[str] = "setup"

    host: Annotated[str, Arg(help="Address of the database VM, e.g. its private IPv6 address.")]
    port: Annotated[int, Arg(help="Port the remote MariaDB listens on.")] = 3306
    agent_port: Annotated[int, Arg(help="Port the Pilot agent listens on.")] = 7781
    admin_user: Annotated[str, Arg(help="Admin account Pilot creates on the remote server.")] = "pilot"
    migrate: Annotated[
        bool,
        Arg(help="Copy the databases of existing sites to the remote server. The benches stop for the copy."),
    ] = False
    delete_local_data: Annotated[
        bool, Arg(help="With --migrate, delete the local server's data after the copy. Kept by default.")
    ] = False

    def run(self) -> None:
        """Set up the remote database server."""
        self.bench.setup_remote_database(
            host=self.host,
            agent_token=self.read_agent_token(),
            port=self.port,
            agent_port=self.agent_port,
            admin_user=self.admin_user,
            on_progress=self.report,
            migrate=self.migrate,
            delete_local_data=self.delete_local_data,
        )
        self.report(f"This host now uses the MariaDB server on {self.host}.")

    def read_agent_token(self) -> str:
        """The token `pilot setup database-server` printed. It stays off the command line, where every
        process on the host can read it."""
        import getpass

        token = getpass.getpass("Pilot agent token: ") if sys.stdin.isatty() else sys.stdin.readline()
        if not token.strip():
            raise BenchError("Give the Pilot agent's token from `pilot setup database-server`.")
        return token.strip()
