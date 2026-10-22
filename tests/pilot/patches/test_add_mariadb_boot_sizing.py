import runpy
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from pilot.config import BenchConfig
from pilot.exceptions import DatabaseError
from pilot.internal.patch_state import is_applied
from pilot.patches import add_mariadb_boot_sizing


def test_patch_runs_when_executed_as_a_script(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    (root / "bench.toml").write_text("")
    manager = Mock()
    manager.is_provisioned.return_value = True
    with (
        patch("pilot.managers.platform.is_linux", return_value=True),
        patch("pilot.utils.benches_dir", return_value=tmp_path),
        patch.object(BenchConfig, "read", return_value=BenchConfig.default("managed", benches_root=tmp_path)),
        patch("pilot.managers.database.mariadb.MariaDBManager", return_value=manager),
    ):
        runpy.run_path(add_mariadb_boot_sizing.__file__, run_name="__main__")
    manager.memory.install_existing.assert_called_once()
    assert is_applied(root, add_mariadb_boot_sizing.PATCH_NAME)


def test_boot_sizing_patch_is_repeatable_and_leaves_external_servers_alone(tmp_path: Path) -> None:
    configs = {}
    for name in ("managed", "external", "sqlite"):
        root = tmp_path / name
        root.mkdir()
        (root / "bench.toml").write_text("")
        config = BenchConfig.default(name, benches_root=tmp_path)
        config.mariadb.existing = name == "external"
        if name == "sqlite":
            config.db_type = "sqlite"
        configs[name] = config
    manager = Mock()
    manager.is_provisioned.return_value = True
    with (
        patch("pilot.managers.platform.is_linux", return_value=True),
        patch.object(BenchConfig, "read", side_effect=lambda path: configs[path.name]),
        patch("pilot.managers.database.mariadb.MariaDBManager", return_value=manager),
    ):
        add_mariadb_boot_sizing.run(tmp_path)
        add_mariadb_boot_sizing.run(tmp_path)
    manager.memory.install_existing.assert_called_once()
    manager.restart.assert_not_called()
    assert all(is_applied(tmp_path / name, add_mariadb_boot_sizing.PATCH_NAME) for name in configs)


def test_failed_boot_hook_installation_is_not_marked_applied(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    root.mkdir()
    (root / "bench.toml").write_text("")
    manager = Mock()
    manager.is_provisioned.return_value = True
    manager.memory.install_existing.side_effect = DatabaseError("installation failed")
    with (
        patch("pilot.managers.platform.is_linux", return_value=True),
        patch.object(BenchConfig, "read", return_value=BenchConfig.default("managed")),
        patch("pilot.managers.database.mariadb.MariaDBManager", return_value=manager),
        pytest.raises(DatabaseError, match="installation failed"),
    ):
        add_mariadb_boot_sizing.run(tmp_path)
    assert not is_applied(root, add_mariadb_boot_sizing.PATCH_NAME)
