from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config import MariaDBConfig
from pilot.managers.database.dedicated_mariadb import DedicatedMariaDBManager


@pytest.fixture(autouse=True)
def root(tmp_path: Path):
    with patch("pilot.core.server.Server.root", tmp_path):
        yield tmp_path


@pytest.fixture
def manager(root: Path) -> DedicatedMariaDBManager:
    manager = DedicatedMariaDBManager(MariaDBConfig(port=3306, dedicated=True))
    manager.config_dir.mkdir(parents=True)
    manager.data_dir.mkdir(parents=True)
    manager.error_log_path.parent.mkdir(parents=True)
    manager.write_managed_config(manager._render_managed_options({}))
    return manager
