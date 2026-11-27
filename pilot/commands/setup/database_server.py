from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, BenchMode, Command


@dataclass(kw_only=True)
class SetupDatabaseServerCommand(Command):
    """Make this VM the dedicated MariaDB server of one app VM."""

    name: ClassVar[str] = "database-server"
    help: ClassVar[str] = "Run MariaDB for the whole VM and the agent the app VM's Pilot manages it through."
    group: ClassVar[str] = "setup"
    bench_mode: ClassVar[BenchMode] = BenchMode.NONE

    client_address: Annotated[str, Arg(help="Address of the app VM, e.g. its private IPv6 address.")]
    port: Annotated[int, Arg(help="Port MariaDB listens on.")] = 3306
    agent_port: Annotated[int, Arg(help="Port the Pilot agent listens on.")] = 7781

    def run(self) -> None:
        """Set up the database server and print the agent's token."""
        from pilot.core.agent.roles.database.setup import DatabaseServerSetup

        token = DatabaseServerSetup(self.client_address, self.port, self.agent_port).run(self.report)
        self.report("This VM is now a database server. On the app VM, run")
        self.report("  pilot setup remote-database <address of this VM>")
        self.report("and give it this token. It is shown only once:")
        self.report(f"  {token}")
