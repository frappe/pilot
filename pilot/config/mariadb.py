from dataclasses import dataclass

from pilot.exceptions import ConfigError

MEMORY_SHARE_RANGE = (0.2, 0.8)


@dataclass
class MariaDBConfig:
    # existing is a deliberate user choice, never inferred from host (see MariaDBManager).
    host: str = "localhost"
    # Avoids clashing with a stock MariaDB/MySQL install on the well-known 3306.
    port: int = 3310
    root_password: str = ""
    admin_user: str = "root"
    socket_path: str = ""
    existing: bool = False
    # A server on its own VM that Pilot manages through the Pilot agent there. An existing server is only
    # connected to.
    remote: bool = False
    agent_port: int = 7781
    agent_token: str = ""
    # This host is that VM: the server has the VM to itself and serves clients on the network.
    dedicated: bool = False
    memory_share: float = 0.5
    memory_high_mb: int | None = None
    memory_max_mb: int | None = None

    def validate(self) -> None:
        """Raise when the memory share is out of range or the server's kind is incomplete."""
        low, high = MEMORY_SHARE_RANGE
        if not low <= self.memory_share <= high:
            raise ConfigError(f"mariadb.memory_share must be between {low} and {high}.")
        if sum((self.existing, self.remote, self.dedicated)) > 1:
            raise ConfigError("Set only one of mariadb.existing, mariadb.remote and mariadb.dedicated.")
        if self.remote:
            self._validate_remote()

    def _validate_remote(self) -> None:
        """Raise unless a remote server names its host and the Pilot agent's token."""
        if self.host in ("", "localhost", "127.0.0.1", "::1"):
            raise ConfigError("mariadb.host must name the remote server.")
        if not self.agent_token:
            raise ConfigError("mariadb.agent_token is required for a remote server.")
