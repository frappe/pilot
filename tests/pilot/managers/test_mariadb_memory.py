from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, PropertyMock, patch

import pytest

from pilot.config.common import CommonConfig
from pilot.config.mariadb import MariaDBConfig
from pilot.core.mariadb_memory import calculate_mariadb_memory
from pilot.exceptions import DatabaseError
from pilot.internal.atomic_file import exclusive_file_lock
from pilot.managers.database.mariadb import MariaDBManager
from pilot.managers.database.memory import MariaDBMemory


@pytest.mark.parametrize("snapshot_isolation", ["ON", "OFF"])
def test_real_manager_resizes_at_boot_and_preserves_custom_variables(
    tmp_path: Path, snapshot_isolation: str
) -> None:
    manager = MariaDBManager(MariaDBConfig())
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "my.cnf").write_text("[mysqld]\n")
    (config_dir / "managed.cnf").write_text(
        "[mysqld]\ninnodb-buffer-pool-size = 400M\nconnect-timeout = 30\n"
        f"innodb-snapshot-isolation = {snapshot_isolation}\n"
    )
    (config_dir / "memory-sizing.json").write_text(
        json.dumps({"boot_id": "previous-boot", "total_memory_mb": 1024})
    )
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    unit = unit_dir / "pilot-mariadb.service"
    unit.write_text("[Unit]\nDescription=MariaDB\n[Service]\n")
    benches = tmp_path / "benches"
    benches.mkdir()
    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch.object(type(manager), "user_unit_dir", new_callable=PropertyMock, return_value=unit_dir),
        patch.object(manager, "_require_linux_managed_server"),
        patch.object(manager, "_total_memory_mb", return_value=2048),
        patch.object(MariaDBMemory, "boot_id", new_callable=PropertyMock, return_value="test-boot"),
        patch("pilot.managers.database.memory.benches_dir", return_value=benches),
        patch("pilot.managers.database.memory.run_command") as memory_commands,
        patch("pilot.managers.database.mariadb.run_command") as manager_commands,
    ):
        manager.memory.prepare_boot()
        assert "Requires=pilot-mariadb-sizing.service" in unit.read_text()
        assert "connect-timeout = 30" in manager.managed_cnf_path.read_text()
        assert manager._read_managed_options()["innodb-snapshot-isolation"] == snapshot_isolation
        assert manager.my_cnf_path.read_text().rstrip().endswith(manager._managed_include_line)
        assert "innodb-buffer-pool-size = 400M" not in manager.managed_cnf_path.read_text()
        assert f"MemoryMax={calculate_mariadb_memory(2048).memory_max_mb}M" in unit.read_text()
        assert (unit_dir / "pilot-mariadb-sizing.service").is_file()
        commands = memory_commands.call_args_list + manager_commands.call_args_list
        assert not any("daemon-reload" in call.args[0] for call in commands)
        assert any("set-property" in call.args[0] for call in commands)


@pytest.mark.parametrize(("raw_value", "expected"), [(b"2097152\n", 2), (b"infinity\n", None)])
def test_real_manager_reads_live_systemd_memory_in_mib(raw_value, expected) -> None:
    manager = MariaDBManager(MariaDBConfig())
    with patch("pilot.managers.database.mariadb.run_command", return_value=Mock(stdout=raw_value)):
        assert manager._unit_memory_mb("MemoryMax") == expected


@pytest.mark.parametrize(
    ("old_share", "new_share", "released"), [(0.5, 0.7, True), (0.7, 0.3, True), (0.7, 0.3, False)]
)
def test_memory_split_limits_survive_an_unchanged_ram_reboot(
    tmp_path: Path, old_share: float, new_share: float, released: bool
) -> None:
    manager = MariaDBManager(MariaDBConfig(memory_share=new_share))
    previous = calculate_mariadb_memory(8192, old_share)
    sizing = calculate_mariadb_memory(8192, new_share)
    units = tmp_path / "units"
    units.mkdir()
    benches = tmp_path / "benches"
    benches.mkdir()
    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch.object(type(manager), "user_unit_dir", new_callable=PropertyMock, return_value=units),
        patch.object(manager, "_require_linux_managed_server"),
        patch.object(manager, "_total_memory_mb", return_value=8192),
        patch.object(manager, "_drop_oversized_buffer_pool_override"),
        patch.object(manager, "_write_config", return_value=sizing),
        patch.object(manager, "_wait_until_healthy"),
        patch.object(manager, "_unit_memory_mb", return_value=previous.memory_max_mb),
        patch.object(manager, "_apply_live_sizing"),
        patch.object(manager, "_has_released_memory", return_value=released),
        patch.object(manager, "_set_runtime_memory_limits") as apply_limits,
        patch.object(MariaDBMemory, "boot_id", new_callable=PropertyMock, return_value="new-boot"),
        patch("pilot.managers.database.memory.benches_dir", return_value=benches),
        patch("pilot.managers.database.mariadb.run_command"),
    ):
        manager.unit_path.write_text(manager._render_unit(previous))
        manager.memory.write_limits(previous.memory_high_mb, previous.memory_max_mb)
        manager.memory.state_path.parent.mkdir(parents=True)
        manager.memory.state_path.write_text(json.dumps({"boot_id": "old-boot", "total_memory_mb": 8192}))
        manager.tune_to_host_locked()
        manager.memory.prepare_boot()
        expected = sizing
        assert f"MemoryMax={expected.memory_max_mb}M" in manager.unit_path.read_text()
        assert manager.memory.limits_path.read_text() == (
            f"[Service]\nMemoryHigh={expected.memory_high_mb}M\nMemoryMax={expected.memory_max_mb}M\n"
        )
        apply_limits.assert_called_with(sizing)


def test_installation_starts_sizing_outside_the_lock_before_the_first_restart(tmp_path: Path) -> None:
    manager = MariaDBManager(MariaDBConfig())
    units = tmp_path / "units"
    units.mkdir()
    unit = units / "pilot-mariadb.service"
    unit.write_text("[Unit]\nDescription=MariaDB\n[Service]\nMemoryMax=6000M\n")
    active = False

    def run(command, **kwargs):
        nonlocal active
        if "start" in command:
            with exclusive_file_lock(manager.action_lock_path, blocking=False):
                pass
            manager.memory.prepare_boot()
            active = True

    def restart():
        assert active
        manager.memory.prepare_boot()

    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch.object(type(manager), "user_unit_dir", new_callable=PropertyMock, return_value=units),
        patch.object(manager, "_require_linux_managed_server"),
        patch.object(manager, "_require_managed_server"),
        patch.object(manager, "_total_memory_mb", return_value=8000),
        patch.object(MariaDBMemory, "boot_id", new_callable=PropertyMock, return_value="current-boot"),
        patch("pilot.managers.database.memory.run_command", side_effect=run),
        patch.object(manager, "_restart_and_wait_healthy", side_effect=restart),
    ):
        manager.memory.install_existing()
        manager.restart_managed_server()
    assert "MemoryMax=6000M" in unit.read_text()


@pytest.fixture
def memory(tmp_path: Path):
    manager = Mock()
    manager.config_dir = tmp_path / "config"
    manager.config_dir.mkdir()
    manager.user_unit_dir = tmp_path / "units"
    manager.user_unit_dir.mkdir()
    manager._UNIT_NAME = "pilot-mariadb.service"
    manager.unit_path = manager.user_unit_dir / manager._UNIT_NAME
    manager.managed_cnf_path = manager.config_dir / "managed.cnf"
    manager.managed_cnf_path.write_text("custom overrides")
    manager.database_action_lock.side_effect = nullcontext
    manager._total_memory_mb.return_value = 8000
    manager.config.memory_share = 0.5
    manager._write_config.return_value = calculate_mariadb_memory(8000)
    manager._read_managed_options.return_value = {
        "innodb-buffer-pool-size": "400M",
        "max-connections": "90",
        "connect-timeout": "30",
        "innodb-snapshot-isolation": "ON",
    }
    manager._render_managed_options.side_effect = json.dumps
    manager.innodb_buffer_pool_size_mb.return_value = 1000
    limits = {"MemoryHigh": 2500, "MemoryMax": 3000, "MemoryCurrent": 1500}
    manager._unit_memory_mb.side_effect = limits.get
    memory = MariaDBMemory(manager)
    (tmp_path / "benches").mkdir()
    with (
        patch.object(MariaDBMemory, "boot_id", new_callable=PropertyMock, return_value="boot-two"),
        patch("pilot.managers.database.memory.benches_dir", return_value=tmp_path / "benches"),
        patch("pilot.managers.database.memory.run_command"),
    ):
        yield memory


@pytest.mark.parametrize(
    ("boot", "capacity", "reset"),
    [
        ("boot-one", 4000, True),
        ("boot-one", 16000, True),
        ("boot-one", 8000, False),
        ("boot-two", 4000, False),
        ("boot-two", 8000, False),
    ],
)
def test_boot_resets_only_on_a_new_boot_with_changed_ram(memory, boot, capacity, reset) -> None:
    memory.state_path.write_text(json.dumps({"boot_id": boot, "total_memory_mb": capacity}))
    memory.prepare_boot()
    assert memory.manager._write_config.called is reset
    if reset:
        persisted = json.loads(memory.manager.managed_cnf_path.read_text())
        assert persisted == {"connect-timeout": "30", "innodb-snapshot-isolation": "ON"}
        memory.manager._install_unit.assert_called_once_with(
            memory.manager._write_config.return_value, preserve_swap_limit=True, reload_manager=False
        )
    else:
        assert memory.manager.managed_cnf_path.read_text() == "custom overrides"


def test_first_allocation_is_automatic_and_repeating_it_preserves_manual_values(memory) -> None:
    memory.prepare_boot()
    memory.manager._write_config.assert_called_once()
    memory.manager._write_config.reset_mock()
    memory.manager.managed_cnf_path.write_text("new manual settings")
    memory.prepare_boot()
    memory.manager._write_config.assert_not_called()
    assert memory.manager.managed_cnf_path.read_text() == "new manual settings"


def test_unchanged_ram_replaces_legacy_limits_with_computed_limits(memory) -> None:
    root = memory.manager.config_dir.parent / "benches"
    (root / "common_config.toml").write_text(
        "[mariadb]\nmemory_share = 0.5\nmemory_high_mb = 3500\nmemory_max_mb = 4500\n"
    )
    memory.manager.config = CommonConfig.read(root).mariadb
    memory.write_limits(3500, 4500)
    memory.state_path.write_text(json.dumps({"boot_id": "previous-boot", "total_memory_mb": 8000}))
    memory.prepare_boot()
    sizing = calculate_mariadb_memory(8000)
    memory.manager._install_unit.assert_called_once_with(
        sizing, preserve_swap_limit=True, reload_manager=False
    )
    memory.manager._set_runtime_memory_limits.assert_called_once_with(sizing)
    memory.manager._write_config.assert_not_called()
    assert "memory_high_mb" not in (root / "common_config.toml").read_text()
    assert "memory_max_mb" not in (root / "common_config.toml").read_text()


def test_boot_failure_does_not_mark_capacity_as_applied(memory) -> None:
    memory.manager._install_unit.side_effect = DatabaseError("write failed")
    with pytest.raises(DatabaseError, match="write failed"):
        memory.prepare_boot()
    assert not memory.state_path.exists()


def test_install_existing_preserves_custom_limits_and_is_repeatable(memory) -> None:
    memory.manager.unit_path.write_text(
        "[Unit]\nDescription=MariaDB\n[Service]\nMemoryMax=6000M\nMemorySwapMax=900M\n"
    )
    memory.install_existing()
    memory.install_existing()
    unit = memory.manager.unit_path.read_text()
    assert unit.count("Requires=pilot-mariadb-sizing.service") == 1
    assert "MemoryMax=6000M" in unit and "MemorySwapMax=900M" in unit
    memory.manager._write_config.assert_not_called()
    memory.prepare_boot()
    memory.manager._write_config.assert_not_called()
    service = (memory.manager.user_unit_dir / "pilot-mariadb-sizing.service").read_text()
    assert "RemainAfterExit=yes" in service and "Before=pilot-mariadb.service" in service


def test_the_sized_memory_includes_the_aria_page_cache() -> None:
    sizing = calculate_mariadb_memory(1962)
    assert sizing.aria_pagecache_mb == sizing.key_buffer_mb == 32
    startup_mb = sizing.innodb_buffer_pool_mb + sizing.key_buffer_mb + sizing.aria_pagecache_mb + 16
    assert startup_mb + 100 < sizing.memory_high_mb


def test_an_existing_server_gets_its_aria_page_cache_sized_once(tmp_path: Path) -> None:
    manager = MariaDBManager(MariaDBConfig())
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "my.cnf").write_text("[mysqld]\nkey-buffer-size = 32M\n")
    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch("pilot.managers.database.mariadb.is_macos", return_value=False),
        patch.object(manager, "_total_memory_mb", return_value=1962),
        patch.object(manager, "database_action_lock", return_value=nullcontext()),
        patch.object(manager, "_write_config", wraps=manager._write_config) as write_config,
    ):
        manager.size_aria_pagecache()
        assert "aria-pagecache-buffer-size = 32M" in manager.my_cnf_path.read_text()
        manager.size_aria_pagecache()
        write_config.assert_called_once()
