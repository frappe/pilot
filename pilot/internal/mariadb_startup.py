from __future__ import annotations

from pilot.config.common import CommonConfig
from pilot.managers.database.mariadb import MariaDBManager
from pilot.utils import benches_dir

if __name__ == "__main__":
    MariaDBManager(CommonConfig.read(benches_dir()).mariadb).memory.prepare_boot()
