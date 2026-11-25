from __future__ import annotations

import hashlib
import ipaddress
import secrets
from collections.abc import Callable

from pilot.config.agent import AgentConfig
from pilot.config.common import CommonConfig
from pilot.core.server import Server
from pilot.exceptions import BenchError
from pilot.internal.command import run_command
from pilot.managers.environment import AdminEnvManager
from pilot.managers.systemd_user import systemctl, systemctl_env, user_unit_dir


class AgentSetup:
    """Run the Pilot agent on this VM for one app VM, which manages this VM through it.

    A second run gives the agent a new token.
    """

    unit_name = "pilot-agent.service"

    def __init__(self, client_address: str, port: int = 7781) -> None:
        self.client_address = client_address.strip()
        self.port = port

    def run(self, on_progress: Callable[[str], None] = lambda message: None) -> str:
        """Start the agent. Return its token, which only the app VM keeps."""
        self.check()
        token = secrets.token_urlsafe(32)
        self.save(token)
        on_progress("Installing the Pilot agent")
        AdminEnvManager(Server().root).install_python_deps()
        self.install_unit()
        return token

    def check(self) -> None:
        """Refuse a client that is not an address."""
        try:
            ipaddress.ip_address(self.client_address)
        except ValueError as exc:
            raise BenchError(f"{self.client_address!r} is not an IP address of the app VM.") from exc

    def save(self, token: str) -> None:
        """Write the agent to common_config.toml, where it reads its token and its caller."""
        benches_dir = Server().benches_dir
        benches_dir.mkdir(parents=True, exist_ok=True)
        with CommonConfig.open(benches_dir) as common:
            common.agent = AgentConfig(
                port=self.port,
                token_hash=hashlib.sha256(token.encode()).hexdigest(),
                client_address=self.client_address,
            )

    def get_unit(self) -> str:
        """The agent's user unit. It runs in the admin venv, which has its web server."""
        root = Server().root
        log_file = root / "system" / "logs" / "agent.log"
        return (
            "[Unit]\n"
            "Description=Pilot agent\n"
            "After=pilot-mariadb.service\n\n"
            "[Service]\n"
            f"WorkingDirectory={root}\n"
            "Environment=PYTHONUNBUFFERED=1\n"
            f"Environment=PYTHONPATH={root}\n"
            f"ExecStart={AdminEnvManager(root).python} -m pilot.core.agent\n"
            "Restart=on-failure\n"
            "RestartSec=5s\n"
            f"StandardOutput=append:{log_file}\n"
            f"StandardError=append:{log_file}\n\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        )

    def install_unit(self) -> None:
        """Install and enable the agent, and restart it so it reads the new token and code."""
        unit_dir = user_unit_dir()
        unit_dir.mkdir(parents=True, exist_ok=True)
        (unit_dir / self.unit_name).write_text(self.get_unit())
        env = systemctl_env()
        run_command(systemctl("daemon-reload"), env=env)
        run_command(systemctl("enable", self.unit_name), env=env)
        run_command(systemctl("restart", self.unit_name), env=env)


# An older Pilot runs this module after it updates, so keep this command line stable.
if __name__ == "__main__":
    agent = CommonConfig.read(Server().benches_dir).agent
    AgentSetup(agent.client_address, agent.port).install_unit()
