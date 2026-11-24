from __future__ import annotations

import hashlib
import ipaddress
import secrets
from collections.abc import Callable

from pilot.config import MariaDBConfig
from pilot.config.common import CommonConfig
from pilot.config.database_agent import DatabaseAgentConfig
from pilot.core.server import Server
from pilot.exceptions import BenchError
from pilot.internal.command import run_command
from pilot.managers.database.dedicated_mariadb import DedicatedMariaDBManager
from pilot.managers.environment import AdminEnvManager
from pilot.managers.systemd_user import systemctl, systemctl_env, user_unit_dir


class DatabaseServerSetup:
    """Make this VM the dedicated database server of one app VM: Pilot's MariaDB for the whole VM, and the
    database agent that the app VM's Pilot manages it through.

    A second run keeps the server's data and passwords and gives the agent a new token.
    """

    unit_name = "pilot-database-agent.service"

    def __init__(self, client_address: str, port: int = 3306, agent_port: int = 7781) -> None:
        self.client_address = client_address.strip()
        self.port = port
        self.agent_port = agent_port

    def run(self, on_progress: Callable[[str], None] = lambda message: None) -> str:
        """Set up the server and start the agent. Return the agent's token, which only the app VM keeps."""
        self.check()
        token = secrets.token_urlsafe(32)
        config = self.save(token)
        on_progress("Setting up MariaDB for the whole VM")
        DedicatedMariaDBManager(config.mariadb).provision()
        on_progress("Installing the database agent")
        AdminEnvManager(Server().root).install_python_deps()
        self.install_agent_unit()
        return token

    def check(self) -> None:
        """Refuse a client that is not an address, or a VM that already runs benches."""
        try:
            ipaddress.ip_address(self.client_address)
        except ValueError as exc:
            raise BenchError(f"{self.client_address!r} is not an IP address of the app VM.") from exc
        if benches := [path.name for path, _ in Server().benches.iter_configs()]:
            raise BenchError(
                f"This VM runs benches ({', '.join(benches)}), so it cannot be a database server."
            )

    def save(self, token: str) -> CommonConfig:
        """Write the server and the agent to common_config.toml, where the agent and the sizing unit read them."""
        benches_dir = Server().benches_dir
        benches_dir.mkdir(parents=True, exist_ok=True)
        with CommonConfig.open(benches_dir) as common:
            common.mariadb = MariaDBConfig(
                port=self.port,
                root_password=common.mariadb.root_password or secrets.token_urlsafe(24),
                dedicated=True,
            )
            common.database_agent = DatabaseAgentConfig(
                port=self.agent_port,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                client_address=self.client_address,
            )
        return CommonConfig.read(benches_dir)

    def get_agent_unit(self) -> str:
        """The agent's user unit. It runs in the admin venv, which has its web server."""
        root = Server().root
        log_file = root / "system" / "logs" / "database-agent.log"
        return (
            "[Unit]\n"
            "Description=Pilot database agent\n"
            "After=pilot-mariadb.service\n\n"
            "[Service]\n"
            f"WorkingDirectory={root}\n"
            "Environment=PYTHONUNBUFFERED=1\n"
            f"Environment=PYTHONPATH={root}\n"
            f"ExecStart={AdminEnvManager(root).python} -m pilot.core.database.agent\n"
            "Restart=on-failure\n"
            "RestartSec=5s\n"
            f"StandardOutput=append:{log_file}\n"
            f"StandardError=append:{log_file}\n\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        )

    def install_agent_unit(self) -> None:
        """Install and enable the agent, and restart it so it reads the new token."""
        unit_dir = user_unit_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)
        (unit_dir / self.unit_name).write_text(self.get_agent_unit())
        env = systemctl_env()
        run_command(systemctl("daemon-reload"), env=env)
        run_command(systemctl("enable", self.unit_name), env=env)
        run_command(systemctl("restart", self.unit_name), env=env)


# An older Pilot runs this module after it updates, so keep this command line stable.
if __name__ == "__main__":
    agent = CommonConfig.read(Server().benches_dir).database_agent
    DatabaseServerSetup(agent.client_address, agent_port=agent.port).install_agent_unit()
