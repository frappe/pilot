from __future__ import annotations

from pilot.config.common import CommonConfig
from pilot.managers.database.mariadb import MariaDBManager

if __name__ == "__main__":
    from pilot.core.server import Server

    MariaDBManager(CommonConfig.read(Server().benches_dir).mariadb).memory.prepare_boot()
