from dataclasses import dataclass
from typing import Optional


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
