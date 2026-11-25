from __future__ import annotations

import secrets
from collections.abc import Callable

from pilot.config import MariaDBConfig
from pilot.config.common import CommonConfig
from pilot.core.agent.setup import AgentSetup
from pilot.core.server import Server
from pilot.exceptions import BenchError
from pilot.managers.database.dedicated_mariadb import DedicatedMariaDBManager


class DatabaseServerSetup:
    """Make this VM the dedicated database server of one app VM: Pilot's MariaDB for the whole VM, and the
    Pilot agent that the app VM's Pilot manages it through.

    A second run keeps the server's data and passwords and gives the agent a new token.
    """

    def __init__(self, client_address: str, port: int = 3306, agent_port: int = 7781) -> None:
        self.port = port
        self.agent = AgentSetup(client_address, agent_port)

    def run(self, on_progress: Callable[[str], None] = lambda message: None) -> str:
        """Set up the server and start the agent. Return the agent's token, which only the app VM keeps."""
        self.check()
        mariadb = self.save()
        on_progress("Setting up MariaDB for the whole VM")
        DedicatedMariaDBManager(mariadb).provision()
        return self.agent.run(on_progress)

    def check(self) -> None:
        """Refuse a client that is not an address, or a VM that already runs benches."""
        self.agent.check()
        if benches := [path.name for path, _ in Server().benches.iter_configs()]:
            raise BenchError(
                f"This VM runs benches ({', '.join(benches)}), so it cannot be a database server."
            )

    def save(self) -> MariaDBConfig:
        """Write the server to common_config.toml, where the agent and the sizing unit read it."""
        benches_dir = Server().benches_dir
        benches_dir.mkdir(parents=True, exist_ok=True)
        with CommonConfig.open(benches_dir) as common:
            common.mariadb = MariaDBConfig(
                port=self.port,
                root_password=common.mariadb.root_password or secrets.token_urlsafe(24),
                dedicated=True,
            )
        return common.mariadb
