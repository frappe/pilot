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
    memory_share: float = 0.5
    memory_high_mb: int | None = None
    memory_max_mb: int | None = None

    def validate(self) -> None:
        low, high = MEMORY_SHARE_RANGE
        if not low <= self.memory_share <= high:
            raise ConfigError(f"mariadb.memory_share must be between {low} and {high}.")
