from dataclasses import dataclass


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
    memory_high_mb: int = 0
    memory_swap_max_mb: int = 100
