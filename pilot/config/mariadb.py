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
    # A server on another host that Pilot manages over SSH. An existing server is only connected to.
    remote: bool = False
    ssh_user: str = ""
    ssh_key_path: str = ""
    # The remote server's memory in MiB, from the VM spec Central gives it. It has the VM to itself.
    memory_mb: int = 0
    memory_share: float = 0.5
    memory_high_mb: int | None = None
    memory_max_mb: int | None = None

    def validate(self) -> None:
        """Raise when the memory share is out of range or the remote server is incomplete."""
        low, high = MEMORY_SHARE_RANGE
        if not low <= self.memory_share <= high:
            raise ConfigError(f"mariadb.memory_share must be between {low} and {high}.")
        if self.remote:
            self._validate_remote()

    def _validate_remote(self) -> None:
        """Raise unless a remote server names its host, its SSH login and its memory."""
        if self.existing:
            raise ConfigError("mariadb.remote and mariadb.existing cannot both be set.")
        if self.host in ("", "localhost", "127.0.0.1", "::1"):
            raise ConfigError("mariadb.host must name the remote server.")
        if not self.ssh_user or not self.ssh_key_path:
            raise ConfigError("mariadb.ssh_user and mariadb.ssh_key_path are required for a remote server.")
        if isinstance(self.memory_mb, bool) or not isinstance(self.memory_mb, int) or self.memory_mb <= 0:
            raise ConfigError("mariadb.memory_mb must be the remote server's memory in MiB.")
