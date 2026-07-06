from dataclasses import dataclass
from typing import Optional

SYSTEM_RESERVED_MEMORY_MB = 700
MEMORY_PER_DB_CONNECTION_MB = 35
MIN_BUFFER_POOL_RATIO = 0.20
MAX_BUFFER_POOL_RATIO = 0.70
RECOMMENDED_BUFFER_POOL_RATIO = 0.65
DEFAULT_BUFFER_POOL_RATIO = 0.25


@dataclass
class MariaDBConfig:
    host: str = "localhost"
    port: int = 3306
    root_password: str = ""
    admin_user: str = "root"
    socket_path: str = ""
    version: Optional[str] = None
    # Empty = shared system MariaDB (legacy). When set, this bench gets its own
    # `mariadb@<instance>` systemd instance with a dedicated datadir/socket/port.
    instance: str = ""
    # Datadir for an instance; defaults to the sibling path /var/lib/mysql-<instance>
    # (never nested inside /var/lib/mysql, which a legacy shared server owns).
    data_dir: str = ""
    # Stored in MB for readability in bench.toml; rendered to MariaDB as an
    # innodb_buffer_pool_size value for the bench-owned instance.
    innodb_buffer_pool_size_mb: int = 0


def real_ram_mb(total_ram_mb: int | float) -> float:
    """Press-compatible estimate of RAM actually available to the OS."""
    return max(0.0, 0.972 * float(total_ram_mb) - 218)


def ram_for_mariadb_mb(total_ram_mb: int | float) -> float:
    return max(0.0, real_ram_mb(total_ram_mb) - SYSTEM_RESERVED_MEMORY_MB)


def key_buffer_size_mb(total_ram_mb: int | float) -> int:
    return 128 if ram_for_mariadb_mb(total_ram_mb) > 4096 else 32


def base_memory_mariadb_mb(total_ram_mb: int | float) -> int:
    return key_buffer_size_mb(total_ram_mb) + 32 + 16


def recommended_max_db_connections(total_ram_mb: int | float) -> int:
    return max(50, 5 * round(float(total_ram_mb) / 1024))


def recommended_innodb_buffer_pool_size_mb(total_ram_mb: int | float) -> int:
    available = ram_for_mariadb_mb(total_ram_mb)
    connection_budget = recommended_max_db_connections(total_ram_mb) * MEMORY_PER_DB_CONNECTION_MB
    headroom_based = available - base_memory_mariadb_mb(total_ram_mb) - connection_budget
    press_style_max = min(headroom_based, available * RECOMMENDED_BUFFER_POOL_RATIO)
    # Press recommends for database servers; Pilot VMs may host multiple benches.
    # Keep the setup default modest, while validation still allows higher values.
    conservative_default = max(512, float(total_ram_mb) * DEFAULT_BUFFER_POOL_RATIO)
    return max(1, int(min(conservative_default, press_style_max)))


def innodb_buffer_pool_bounds_mb(total_ram_mb: int | float) -> tuple[int, int]:
    available = ram_for_mariadb_mb(total_ram_mb)
    return max(1, int(available * MIN_BUFFER_POOL_RATIO)), max(1, int(available * MAX_BUFFER_POOL_RATIO))
