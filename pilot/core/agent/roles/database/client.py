from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING, Any

from pilot.core.agent.client import AgentClient
from pilot.exceptions import AgentError, DatabaseError

if TYPE_CHECKING:
    from pilot.config import MariaDBConfig


class DatabaseAgentClient:
    """The database role of the Pilot agent on a remote MariaDB's VM, which runs the operations on it that SQL
    cannot."""

    def __init__(self, agent: AgentClient) -> None:
        self.agent = agent

    @classmethod
    def for_config(cls, config: MariaDBConfig) -> DatabaseAgentClient:
        """The agent beside the configured remote server."""
        return cls(AgentClient(config.host, config.agent_port, config.agent_token))

    def request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> Any:
        """Call a database request of the agent; raise DatabaseError with the agent's reason."""
        try:
            return self.agent.request(method, f"/database{path}", body, timeout)
        except AgentError as error:
            raise DatabaseError(str(error)) from error

    def get_health(self) -> dict[str, Any]:
        """The server's state; raise when the VM is not a database server."""
        try:
            roles = self.agent.get_health()["roles"]
        except AgentError as error:
            raise DatabaseError(str(error)) from error
        if "database" not in roles:
            raise DatabaseError(f"The VM {self.agent.host} is not a database server.")
        return roles["database"]

    def read_managed_config(self) -> str:
        """The variable editor's option file."""
        return self.request("GET", "/config/managed")["content"]

    def write_managed_config(self, content: str) -> None:
        """Replace the variable editor's option file."""
        self.request("PUT", "/config/managed", {"content": content})

    def control(self, action: str, timeout: float) -> None:
        """Start, stop or restart the server."""
        self.request("POST", f"/service/{action}", timeout=timeout)

    def get_file(self, path: str, tail_bytes: int = 0) -> dict[str, Any]:
        """Whether a server file exists, its size, and its last `tail_bytes` as text."""
        query = urllib.parse.urlencode({"path": path, "tail_bytes": tail_bytes})
        return self.request("GET", f"/files?{query}")

    def get_free_disk_bytes(self) -> int:
        """Free bytes on the disk that holds the data directory."""
        return self.request("GET", "/disk")["free_bytes"]

    def grant_admin(self, user: str, password: str) -> str:
        """Create or reset the admin account for this host's address; return that address."""
        return self.request("POST", "/accounts/admin", {"user": user, "password": password})["host"]
