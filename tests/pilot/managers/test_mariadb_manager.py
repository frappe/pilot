from __future__ import annotations

import stat
import subprocess
from unittest.mock import MagicMock, Mock, PropertyMock, call, patch

import pytest

from pilot.config import MariaDBConfig
from pilot.core.mariadb_memory import (
    calculate_mariadb_memory,
    calculate_mariadb_variable_limits,
)
from pilot.exceptions import DatabaseError
from pilot.managers.database.mariadb import MariaDBManager

MODULE = "pilot.managers.database.mariadb"
BASE_MODULE = "pilot.managers.database.base"


def _manager(password: str = "root") -> MariaDBManager:
    return MariaDBManager(MariaDBConfig(root_password=password))


def test_socket_path_defaults_under_state_dir() -> None:
    assert _manager().socket_path.endswith("/databases/mariadb/run/mysqld.sock")


def test_socket_path_honors_explicit_value() -> None:
    assert MariaDBManager(MariaDBConfig(socket_path="/tmp/custom.sock")).socket_path == "/tmp/custom.sock"


def test_external_server_does_not_infer_pilot_managed_socket(tmp_path) -> None:
    manager = MariaDBManager(MariaDBConfig(host="db.example.com", existing=True))
    socket = tmp_path / "run" / "mysqld.sock"
    socket.parent.mkdir()
    socket.touch()
    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch(f"{MODULE}.is_macos", return_value=False),
    ):
        assert manager._detect_socket() == ""


def test_default_port_avoids_standard_mysql_port() -> None:
    assert MariaDBConfig().port == 3310


def test_press_unified_memory_sizing_is_adapted_for_pilot() -> None:
    sizing = calculate_mariadb_memory(8192)

    assert sizing.mariadb_memory_mb == 3172
    assert sizing.innodb_buffer_pool_mb == 1692
    assert sizing.max_connections == 50
    assert sizing.key_buffer_mb == 32
    assert sizing.innodb_log_file_mb == 512
    assert sizing.memory_high_mb == 2148
    assert sizing.memory_max_mb == 3172


def test_small_vm_limits_leave_memory_for_other_pilot_processes() -> None:
    sizing = calculate_mariadb_memory(2048)

    assert sizing.innodb_buffer_pool_mb == 128
    assert sizing.max_connections == 50
    assert sizing.innodb_log_file_mb == 48
    assert sizing.memory_high_mb == 384
    assert sizing.memory_max_mb == 512
    assert sizing.memory_max_mb < sizing.total_memory_mb


def test_small_vm_variable_limits_use_mariadb_ceiling_not_full_host() -> None:
    limits = calculate_mariadb_variable_limits(2048)

    assert limits.innodb_buffer_pool_min_mb == 128
    assert limits.innodb_buffer_pool_max_mb == 352
    assert limits.innodb_buffer_pool_recommended_mb == 128
    assert limits.max_connections_min == 10
    assert limits.max_connections_max == 50
    assert limits.max_connections_recommended == 50


def test_larger_vm_variable_limits_preserve_press_memory_guards() -> None:
    limits = calculate_mariadb_variable_limits(8192)

    assert limits.innodb_buffer_pool_min_mb == 640
    assert limits.innodb_buffer_pool_max_mb == 2216
    assert limits.innodb_buffer_pool_recommended_mb == 1692
    assert limits.max_connections_min == 10
    assert limits.max_connections_max == 90
    assert limits.max_connections_recommended == 50


@pytest.mark.parametrize("total_memory_mb", [1024, 2048, 3072, 4096, 8192, 16384, 32768])
def test_startup_values_are_inside_configurable_ranges(total_memory_mb: int) -> None:
    sizing = calculate_mariadb_memory(total_memory_mb)
    limits = calculate_mariadb_variable_limits(total_memory_mb)

    assert (
        limits.innodb_buffer_pool_min_mb <= sizing.innodb_buffer_pool_mb <= limits.innodb_buffer_pool_max_mb
    )
    assert limits.max_connections_min <= sizing.max_connections <= limits.max_connections_max


@pytest.mark.parametrize("total_memory_mb", [256, 512, 1024, 2048, 8192])
def test_memory_limits_never_claim_more_than_half_the_host(total_memory_mb: int) -> None:
    sizing = calculate_mariadb_memory(total_memory_mb)

    assert 0 < sizing.memory_high_mb <= sizing.memory_max_mb
    assert sizing.memory_max_mb <= total_memory_mb // 2


@pytest.mark.parametrize(
    ("total_memory_mb", "expected_log_file_mb"),
    [(4096, 128), (8192, 512), (16384, 1024), (32768, 2048)],
)
def test_log_file_size_uses_press_memory_bands(
    total_memory_mb: int,
    expected_log_file_mb: int,
) -> None:
    assert calculate_mariadb_memory(total_memory_mb).innodb_log_file_mb == expected_log_file_mb


def test_existing_defaults_to_false() -> None:
    assert MariaDBConfig().existing is False


def test_existing_is_not_inferred_from_host() -> None:
    assert MariaDBConfig(host="db.example.com").existing is False


def test_install_raises_when_missing_on_linux() -> None:
    m = _manager()
    with (
        patch.object(m, "is_installed", return_value=False),
        patch(f"{BASE_MODULE}.is_macos", return_value=False),
        pytest.raises(DatabaseError, match=r"install\.sh"),
    ):
        m.install()


def test_start_targets_systemctl_user_on_linux() -> None:
    m = _manager()
    with (
        patch(f"{BASE_MODULE}.is_macos", return_value=False),
        patch(f"{BASE_MODULE}.run_command") as rc,
    ):
        m.start()
    assert rc.call_args.args[0] == ["systemctl", "--user", "start", "pilot-mariadb.service"]


def test_provision_initialises_and_installs_unit_when_fresh(tmp_path) -> None:
    m = _manager()
    with (
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(m, "install"),
        patch.object(type(m), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch.object(m, "is_provisioned", return_value=False),
        patch.object(m, "is_running", return_value=False),
        patch.object(m, "_total_memory_mb", return_value=8192),
        patch.object(m, "_install_unit") as install_unit,
        patch.object(m, "_reset_failed_state") as reset_failed_state,
        patch.object(m, "_wait_until_reachable"),
        patch.object(m, "secure_installation") as secure,
        patch(f"{MODULE}.run_command") as rc,
        patch(f"{BASE_MODULE}.run_command"),
    ):
        m.provision()
        assert m.my_cnf_path.read_text().startswith("# Managed by Pilot.\n[mysqld]\n")
    install_unit.assert_called_once()
    reset_failed_state.assert_called_once()
    secure.assert_called_once()
    argv_calls = [c.args[0] for c in rc.call_args_list]
    assert any("mariadb-install-db" in argv for argv in argv_calls)


def test_macos_provision_does_not_write_linux_configuration() -> None:
    manager = _manager()
    with (
        patch(f"{MODULE}.is_macos", return_value=True),
        patch.object(manager, "install"),
        patch.object(manager, "_provision_macos") as provision_macos,
        patch.object(manager, "_write_config") as write_config,
    ):
        manager.provision()

    provision_macos.assert_called_once()
    write_config.assert_not_called()


def test_write_config_contains_all_server_settings(tmp_path) -> None:
    manager = MariaDBManager(MariaDBConfig(port=4306))
    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch.object(manager, "_total_memory_mb", return_value=8192),
    ):
        sizing = manager._write_config()

    option_file = tmp_path / "config" / "my.cnf"
    managed_file = tmp_path / "config" / "managed.cnf"
    content = option_file.read_text()
    assert stat.S_IMODE(option_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(managed_file.stat().st_mode) == 0o600
    assert managed_file.read_text() == "# Managed by Pilot's database variable editor.\n[mysqld]\n"
    assert f"!include {managed_file}" in content
    assert f"datadir = {tmp_path / 'data'}" in content
    assert f"socket = {tmp_path / 'run' / 'mysqld.sock'}" in content
    assert f"pid-file = {tmp_path / 'run' / 'mysqld.pid'}" in content
    assert "bind-address = 127.0.0.1" in content
    assert "port = 4306" in content
    assert "character-set-server = utf8mb4" in content
    assert "collation-server = utf8mb4_unicode_ci" in content
    assert "local-infile = OFF" in content
    assert "innodb-stats-persistent-sample-pages = 256" in content
    assert "innodb-snapshot-isolation = OFF" in content
    assert "slave-connections-needed-for-purge = 0" in content
    assert f"innodb-buffer-pool-size = {sizing.innodb_buffer_pool_mb}M" in content
    assert "innodb-buffer-pool-size-max = 2216M" in content
    assert "innodb-buffer-pool-size-auto-min = 640M" in content
    assert f"innodb-log-file-size = {sizing.innodb_log_file_mb}M" in content
    assert f"key-buffer-size = {sizing.key_buffer_mb}M" in content
    assert f"max-connections = {sizing.max_connections}" in content

    for unsupported_or_unmanaged_option in (
        "innodb-file-format",
        "innodb-file-per-table",
        "innodb-flush-method",
        "innodb-large-prefix",
        "log-bin",
        "query-cache",
        "slow-query-log",
    ):
        assert unsupported_or_unmanaged_option not in content


def test_write_config_creates_private_managed_option_file(tmp_path) -> None:
    manager = _manager()
    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch.object(manager, "_total_memory_mb", return_value=8192),
    ):
        manager._write_config()

    managed_file = tmp_path / "config" / "managed.cnf"
    assert stat.S_IMODE(managed_file.stat().st_mode) == 0o600
    assert managed_file.read_text() == "# Managed by Pilot's database variable editor.\n[mysqld]\n"
    assert f"!include {managed_file}" in (tmp_path / "config" / "my.cnf").read_text()


def test_existing_option_file_is_migrated_to_managed_include_once(tmp_path) -> None:
    manager = _manager()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    option_file = config_dir / "my.cnf"
    option_file.write_text("# Managed by Pilot.\n[mysqld]\nport = 3310\n")

    with patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path):
        manager.ensure_managed_config()
        manager.ensure_managed_config()

    include = f"!include {config_dir / 'managed.cnf'}"
    assert option_file.read_text().splitlines().count(include) == 1
    assert stat.S_IMODE(option_file.stat().st_mode) == 0o600
    assert stat.S_IMODE((config_dir / "managed.cnf").stat().st_mode) == 0o600


def test_performance_schema_change_preserves_other_managed_options(tmp_path) -> None:
    manager = _manager()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "my.cnf").write_text("# Managed by Pilot.\n[mysqld]\n")
    (config_dir / "managed.cnf").write_text(
        "# Managed by Pilot's database variable editor.\n" "[mysqld]\n" "max-connections = 50\n"
    )

    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(manager, "is_installed", return_value=True),
        patch.object(manager, "is_provisioned", return_value=True),
        patch.object(manager, "is_healthy", return_value=True),
        patch.object(manager, "performance_schema_enabled", side_effect=[False, True]),
        patch.object(manager, "_restart_and_wait_healthy") as restart,
    ):
        assert manager.set_performance_schema(True) is True

    managed = (config_dir / "managed.cnf").read_text()
    assert "max-connections = 50" in managed
    assert "performance-schema = ON" in managed
    restart.assert_called_once()


def test_performance_schema_noop_does_not_restart_or_write(tmp_path) -> None:
    manager = _manager()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "my.cnf").write_text("# Managed by Pilot.\n[mysqld]\n")
    managed_file = config_dir / "managed.cnf"
    managed_file.write_text("# Managed by Pilot's database variable editor.\n[mysqld]\n")
    before = managed_file.read_text()

    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(manager, "is_installed", return_value=True),
        patch.object(manager, "is_provisioned", return_value=True),
        patch.object(manager, "is_healthy", return_value=True),
        patch.object(manager, "performance_schema_enabled", return_value=True),
        patch.object(manager, "_restart_and_wait_healthy") as restart,
    ):
        assert manager.set_performance_schema(True) is False

    assert managed_file.read_text() == before
    restart.assert_not_called()


def test_performance_schema_uses_task_restart_executor(tmp_path) -> None:
    manager = _manager()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "my.cnf").write_text("# Managed by Pilot.\n[mysqld]\n")
    (config_dir / "managed.cnf").write_text("# Managed by Pilot's database variable editor.\n[mysqld]\n")

    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(manager, "is_installed", return_value=True),
        patch.object(manager, "is_provisioned", return_value=True),
        patch.object(manager, "is_healthy", return_value=True),
        patch.object(manager, "performance_schema_enabled", side_effect=[False, True]),
        patch.object(manager, "_restart_and_wait_healthy") as restart,
    ):
        executor = Mock(side_effect=lambda callback: callback())
        assert manager.set_performance_schema(True, restart_executor=executor) is True

    executor.assert_called_once_with(restart)
    restart.assert_called_once()


def test_failed_performance_schema_change_restores_exact_previous_config(tmp_path) -> None:
    manager = _manager()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "my.cnf").write_text("# Managed by Pilot.\n[mysqld]\n")
    managed_file = config_dir / "managed.cnf"
    previous = "# Managed by Pilot's database variable editor.\n" "[mysqld]\n" "max-connections = 50\n"
    managed_file.write_text(previous)

    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(manager, "is_installed", return_value=True),
        patch.object(manager, "is_provisioned", return_value=True),
        patch.object(manager, "is_healthy", return_value=True),
        patch.object(manager, "performance_schema_enabled", side_effect=[False, False, False]),
        patch.object(manager, "_restart_and_wait_healthy") as restart,
        pytest.raises(DatabaseError, match="previous configuration was restored"),
    ):
        manager.set_performance_schema(True)

    assert managed_file.read_text() == previous
    assert restart.call_count == 2


def test_performance_schema_reports_when_automatic_rollback_also_fails(tmp_path) -> None:
    manager = _manager()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "my.cnf").write_text("# Managed by Pilot.\n[mysqld]\n")
    (config_dir / "managed.cnf").write_text("# Managed by Pilot's database variable editor.\n[mysqld]\n")

    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(manager, "is_installed", return_value=True),
        patch.object(manager, "is_provisioned", return_value=True),
        patch.object(manager, "is_healthy", return_value=True),
        patch.object(manager, "performance_schema_enabled", return_value=False),
        patch.object(
            manager,
            "_restart_and_wait_healthy",
            side_effect=[DatabaseError("apply failed"), DatabaseError("rollback failed")],
        ),
        pytest.raises(
            DatabaseError, match="restoring the previous configuration also failed: rollback failed"
        ),
    ):
        manager.set_performance_schema(True)


def test_performance_schema_rejects_non_boolean_before_touching_server() -> None:
    manager = _manager()
    with (
        patch.object(manager, "is_installed") as installed,
        pytest.raises(DatabaseError, match="either enabled or disabled"),
    ):
        manager.set_performance_schema(1)
    installed.assert_not_called()


def test_innodb_buffer_pool_change_applies_live_and_persists_limits(tmp_path) -> None:
    manager = _manager()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "my.cnf").write_text("# Managed by Pilot.\n[mysqld]\n")
    managed_file = config_dir / "managed.cnf"
    managed_file.write_text("# Managed by Pilot's database variable editor.\n[mysqld]\n")
    mib = 1024 * 1024
    state = {
        "innodb_buffer_pool_size": 128 * mib,
        "innodb_buffer_pool_size_max": 352 * mib,
    }

    def set_global(variable: str, value: int) -> None:
        state[variable] = value

    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(manager, "is_installed", return_value=True),
        patch.object(manager, "is_provisioned", return_value=True),
        patch.object(manager, "is_healthy", return_value=True),
        patch.object(manager, "_total_memory_mb", return_value=2048),
        patch.object(manager, "_read_global_integer", side_effect=lambda variable: state[variable]),
        patch.object(manager, "_set_global_integer", side_effect=set_global) as update,
        patch.object(manager, "_restart_and_wait_healthy") as restart,
    ):
        assert manager.set_innodb_buffer_pool_size(256) is True

    update.assert_called_once_with("innodb_buffer_pool_size", 256 * mib)
    restart.assert_not_called()
    assert "innodb-buffer-pool-size = 256M" in managed_file.read_text()
    assert "innodb-buffer-pool-size-max = 352M" in managed_file.read_text()
    assert "innodb-buffer-pool-size-auto-min = 128M" in managed_file.read_text()


def test_innodb_buffer_pool_increase_restarts_when_live_ceiling_is_too_low(tmp_path) -> None:
    manager = _manager()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "my.cnf").write_text("# Managed by Pilot.\n[mysqld]\n")
    (config_dir / "managed.cnf").write_text("# Managed by Pilot's database variable editor.\n[mysqld]\n")
    mib = 1024 * 1024
    state = {
        "innodb_buffer_pool_size": 128 * mib,
        "innodb_buffer_pool_size_max": 128 * mib,
    }

    def restart() -> None:
        state["innodb_buffer_pool_size"] = 256 * mib
        state["innodb_buffer_pool_size_max"] = 352 * mib

    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(manager, "is_installed", return_value=True),
        patch.object(manager, "is_provisioned", return_value=True),
        patch.object(manager, "is_healthy", return_value=True),
        patch.object(manager, "_total_memory_mb", return_value=2048),
        patch.object(manager, "_read_global_integer", side_effect=lambda variable: state[variable]),
        patch.object(manager, "_set_global_integer") as update,
        patch.object(manager, "_restart_and_wait_healthy", side_effect=restart) as restart_server,
    ):
        assert manager.set_innodb_buffer_pool_size(256) is True

    restart_server.assert_called_once()
    update.assert_not_called()


def test_max_connections_change_applies_live_and_preserves_other_options(tmp_path) -> None:
    manager = _manager()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "my.cnf").write_text("# Managed by Pilot.\n[mysqld]\n")
    managed_file = config_dir / "managed.cnf"
    managed_file.write_text(
        "# Managed by Pilot's database variable editor.\n" "[mysqld]\n" "performance-schema = ON\n"
    )
    state = {"max_connections": 50}

    def set_global(variable: str, value: int) -> None:
        state[variable] = value

    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(manager, "is_installed", return_value=True),
        patch.object(manager, "is_provisioned", return_value=True),
        patch.object(manager, "is_healthy", return_value=True),
        patch.object(manager, "_total_memory_mb", return_value=2048),
        patch.object(manager, "_read_global_integer", side_effect=lambda variable: state[variable]),
        patch.object(manager, "_set_global_integer", side_effect=set_global) as update,
    ):
        assert manager.set_max_connections(40) is True

    update.assert_called_once_with("max_connections", 40)
    assert "max-connections = 40" in managed_file.read_text()
    assert "performance-schema = ON" in managed_file.read_text()


def test_failed_dynamic_variable_change_restores_exact_config_and_runtime(tmp_path) -> None:
    manager = _manager()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "my.cnf").write_text("# Managed by Pilot.\n[mysqld]\n")
    managed_file = config_dir / "managed.cnf"
    previous = "# Managed by Pilot's database variable editor.\n" "[mysqld]\n" "performance-schema = OFF\n"
    managed_file.write_text(previous)

    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(manager, "is_installed", return_value=True),
        patch.object(manager, "is_provisioned", return_value=True),
        patch.object(manager, "is_healthy", return_value=True),
        patch.object(manager, "_total_memory_mb", return_value=2048),
        patch.object(manager, "_read_global_integer", return_value=50),
        patch.object(manager, "_set_global_integer") as update,
        pytest.raises(DatabaseError, match="previous configuration was restored"),
    ):
        manager.set_max_connections(40)

    assert managed_file.read_text() == previous
    update.assert_called_once_with("max_connections", 40)


@pytest.mark.parametrize(
    ("method", "value", "message"),
    [
        ("set_innodb_buffer_pool_size", True, "whole number"),
        ("set_max_connections", "50", "whole number"),
    ],
)
def test_sizing_actions_reject_non_integer_before_touching_server(
    method: str,
    value,
    message: str,
) -> None:
    manager = _manager()
    with (
        patch.object(manager, "is_installed") as installed,
        pytest.raises(DatabaseError, match=message),
    ):
        getattr(manager, method)(value)
    installed.assert_not_called()


def test_global_integer_helpers_allow_only_known_variables() -> None:
    manager = _manager()
    with (
        patch.object(manager, "connect") as connect,
        pytest.raises(DatabaseError, match="cannot change"),
    ):
        manager._set_global_integer("max_connections; DROP DATABASE mysql", 10)
    connect.assert_not_called()


def test_global_variable_catalog_read_uses_parameterized_names() -> None:
    manager = _manager()
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.return_value = [
        ("CONNECT_TIMEOUT", "10"),
        ("INNODB_PRINT_ALL_DEADLOCKS", "ON"),
    ]
    with patch.object(manager, "connect", return_value=connection):
        result = manager.global_variable_values(("connect_timeout", "innodb_print_all_deadlocks"))

    assert result == {
        "connect_timeout": "10",
        "innodb_print_all_deadlocks": "ON",
    }
    sql, parameters = cursor.execute.call_args.args
    assert "IN (%s, %s)" in sql
    assert parameters == ("connect_timeout", "innodb_print_all_deadlocks")
    assert "connect_timeout" not in sql
    connection.close.assert_called_once()


def test_global_variable_catalog_rejects_unknown_name_before_connecting() -> None:
    manager = _manager()
    with (
        patch.object(manager, "connect") as connect,
        pytest.raises(DatabaseError, match="cannot read"),
    ):
        manager.global_variable_values(("connect_timeout; DROP DATABASE mysql",))
    connect.assert_not_called()


def test_safe_configuration_change_applies_live_and_persists(tmp_path) -> None:
    manager = _manager()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "my.cnf").write_text("# Managed by Pilot.\n[mysqld]\n")
    managed_file = config_dir / "managed.cnf"
    managed_file.write_text(
        "# Managed by Pilot's database variable editor.\n" "[mysqld]\n" "performance-schema = OFF\n"
    )
    state = {"connect_timeout": 10}

    def set_value(name, value) -> None:
        state[name] = value

    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(manager, "is_installed", return_value=True),
        patch.object(manager, "is_provisioned", return_value=True),
        patch.object(manager, "is_healthy", return_value=True),
        patch.object(
            manager,
            "_read_configuration_variable",
            side_effect=lambda name: state[name],
        ),
        patch.object(manager, "_set_configuration_variable", side_effect=set_value) as update,
    ):
        assert manager.set_configuration_variable("connect_timeout", 20) is True

    update.assert_called_once_with("connect_timeout", 20)
    assert "connect-timeout = 20" in managed_file.read_text()
    assert "performance-schema = OFF" in managed_file.read_text()


def test_safe_boolean_configuration_is_written_as_on_or_off(tmp_path) -> None:
    manager = _manager()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "my.cnf").write_text("# Managed by Pilot.\n[mysqld]\n")
    managed_file = config_dir / "managed.cnf"
    managed_file.write_text("# Managed by Pilot's database variable editor.\n[mysqld]\n")
    state = {"innodb_print_all_deadlocks": True}

    def set_value(name, value) -> None:
        state[name] = value

    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(manager, "is_installed", return_value=True),
        patch.object(manager, "is_provisioned", return_value=True),
        patch.object(manager, "is_healthy", return_value=True),
        patch.object(
            manager,
            "_read_configuration_variable",
            side_effect=lambda name: state[name],
        ),
        patch.object(manager, "_set_configuration_variable", side_effect=set_value),
    ):
        assert manager.set_configuration_variable("innodb_print_all_deadlocks", False) is True

    assert "innodb-print-all-deadlocks = OFF" in managed_file.read_text()


def test_failed_catalog_change_restores_exact_config_and_runtime(tmp_path) -> None:
    manager = _manager()
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "my.cnf").write_text("# Managed by Pilot.\n[mysqld]\n")
    managed_file = config_dir / "managed.cnf"
    previous = "# Managed by Pilot's database variable editor.\n" "[mysqld]\n" "performance-schema = OFF\n"
    managed_file.write_text(previous)

    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(manager, "is_installed", return_value=True),
        patch.object(manager, "is_provisioned", return_value=True),
        patch.object(manager, "is_healthy", return_value=True),
        patch.object(manager, "_read_configuration_variable", return_value=10),
        patch.object(manager, "_set_configuration_variable") as update,
        patch.object(
            manager,
            "_verify_configuration_variable",
            side_effect=[DatabaseError("verification failed"), None],
        ),
        pytest.raises(DatabaseError, match="previous configuration was restored"),
    ):
        manager.set_configuration_variable("connect_timeout", 20)

    assert managed_file.read_text() == previous
    assert update.call_args_list == [
        call("connect_timeout", 20),
        call("connect_timeout", 10),
    ]


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("connect_timeout", True, "whole number"),
        ("connect_timeout", 1, "at least 2"),
        ("max_connections", 50, "cannot change"),
        ("connect_timeout; DROP DATABASE mysql", 10, "cannot change"),
    ],
)
def test_catalog_change_rejects_invalid_input_before_touching_server(
    name: str,
    value,
    message: str,
) -> None:
    manager = _manager()
    with (
        patch.object(manager, "is_installed") as installed,
        pytest.raises(DatabaseError, match=message),
    ):
        manager.set_configuration_variable(name, value)
    installed.assert_not_called()


def test_database_action_lock_is_released_after_action_failure(tmp_path) -> None:
    manager = _manager()
    with patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path):
        with pytest.raises(RuntimeError, match="action failed"), manager.database_action_lock():
            raise RuntimeError("action failed")

        with manager.database_action_lock():
            pass


def test_database_action_lock_rejects_concurrent_host_action(tmp_path) -> None:
    manager = _manager()
    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        manager.database_action_lock(),
        pytest.raises(DatabaseError, match="Another database action"),
        manager.database_action_lock(),
    ):
        pass


def test_restart_managed_server_verifies_health_under_action_lock(tmp_path) -> None:
    manager = _manager()
    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch.object(manager, "is_installed", return_value=True),
        patch.object(manager, "is_provisioned", return_value=True),
        patch.object(manager, "_restart_and_wait_healthy") as restart,
    ):
        manager.restart_managed_server()
    restart.assert_called_once()


def test_data_directory_initialization_uses_pilot_option_file(tmp_path) -> None:
    manager = _manager()
    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch(f"{MODULE}.run_command") as run,
    ):
        manager._initialize_data_dir()

    assert run.call_args.args[0] == [
        "mariadb-install-db",
        f"--defaults-file={tmp_path / 'config' / 'my.cnf'}",
        f"--datadir={tmp_path / 'data'}",
        "--skip-test-db",
    ]


def test_linux_unit_starts_with_option_file_and_memory_limits(tmp_path) -> None:
    manager = _manager()
    sizing = calculate_mariadb_memory(8192)
    unit_dir = tmp_path / "units"
    with (
        patch.object(type(manager), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch.object(type(manager), "user_unit_dir", new_callable=PropertyMock, return_value=unit_dir),
        patch(f"{MODULE}.which", return_value="/usr/sbin/mariadbd"),
        patch(f"{MODULE}.run_command"),
    ):
        manager._install_unit(sizing)

    content = (unit_dir / "pilot-mariadb.service").read_text()
    assert f"ExecStart=/usr/sbin/mariadbd --defaults-file={tmp_path / 'config' / 'my.cnf'}" in content
    assert "LimitNOFILE=65535" in content
    assert f"MemoryHigh={sizing.memory_high_mb}M" in content
    assert f"MemoryMax={sizing.memory_max_mb}M" in content
    assert "MemorySwapMax=100M" in content


def test_is_provisioned_on_macos_checks_live_server_not_a_marker_file() -> None:
    """macOS provisioning state comes from the live secured server."""
    m = _manager()
    with (
        patch(f"{MODULE}.is_macos", return_value=True),
        patch.object(m, "is_running", return_value=False),
    ):
        assert m.is_provisioned() is False  # not running yet
    with (
        patch(f"{MODULE}.is_macos", return_value=True),
        patch.object(m, "is_running", return_value=True),
        patch.object(m, "is_unsecured", return_value=True),
    ):
        assert m.is_provisioned() is False  # up but still passwordless
    with (
        patch(f"{MODULE}.is_macos", return_value=True),
        patch.object(m, "is_running", return_value=True),
        patch.object(m, "is_unsecured", return_value=False),
    ):
        assert m.is_provisioned() is True  # up and already secured


def test_is_provisioned_false_when_data_dir_wiped_but_unit_still_exists(tmp_path) -> None:
    """A stale systemd unit outliving a deleted state dir must not look provisioned."""
    m = _manager()
    with (
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(type(m), "state_dir", new_callable=PropertyMock, return_value=tmp_path),
        patch(f"{BASE_MODULE}.UserOwnedDBManager.is_provisioned", return_value=True),
    ):
        assert m.is_provisioned() is False


def test_wait_until_reachable_raises_after_timeout() -> None:
    m = _manager()
    with (
        patch.object(m, "is_reachable", return_value=False),
        patch(f"{BASE_MODULE}.time.sleep"),
        pytest.raises(DatabaseError, match="did not become reachable"),
    ):
        m._wait_until_reachable(timeout=0.01)


def test_provision_resets_failed_state_before_restarting_stopped_unit() -> None:
    m = _manager()
    with (
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(m, "install"),
        patch.object(m, "is_provisioned", return_value=True),
        patch.object(m, "is_running", return_value=False),
        patch.object(m, "_wait_until_reachable"),
        patch.object(m, "secure_installation"),
        patch(f"{BASE_MODULE}.run_command") as rc,
        patch(f"{BASE_MODULE}.subprocess.run") as reset_run,
    ):
        m.provision()
    reset_run.assert_called_once()
    assert reset_run.call_args.args[0] == ["systemctl", "--user", "reset-failed", "pilot-mariadb.service"]
    assert rc.call_args.args[0] == ["systemctl", "--user", "enable", "--now", "pilot-mariadb.service"]


def test_provision_reuses_already_provisioned_server_but_still_enables_it() -> None:
    """Nothing is re-initialised, yet enabling still runs: a unit that exists but
    was never enabled starts here and would otherwise die at the next reboot."""
    m = _manager()
    with (
        patch(f"{MODULE}.is_macos", return_value=False),
        patch.object(m, "install"),
        patch.object(m, "is_provisioned", return_value=True),
        patch.object(m, "is_running", return_value=True),
        patch.object(m, "_write_config") as write_config,
        patch.object(m, "_install_unit") as install_unit,
        patch.object(m, "_reset_failed_state"),
        patch.object(m, "_wait_until_reachable"),
        patch.object(m, "secure_installation") as secure,
        patch(f"{MODULE}.run_command") as rc,
        patch(f"{BASE_MODULE}.run_command") as base_rc,
    ):
        m.provision()
    install_unit.assert_not_called()
    write_config.assert_not_called()
    rc.assert_not_called()
    assert base_rc.call_args.args[0] == ["systemctl", "--user", "enable", "--now", "pilot-mariadb.service"]
    secure.assert_called_once()


def test_sql_quote_plain() -> None:
    assert MariaDBManager._sql_quote("hunter2") == "'hunter2'"


def test_sql_quote_escapes_single_quote() -> None:
    assert MariaDBManager._sql_quote("a'b") == "'a\\'b'"


def test_sql_quote_escapes_backslash() -> None:
    assert MariaDBManager._sql_quote("a\\b") == "'a\\\\b'"


def test_secure_installation_noop_when_credentials_valid() -> None:
    manager = _manager()
    with (
        patch.object(manager, "has_valid_credentials", return_value=True),
        patch.object(manager, "_run_sql_as_superuser") as run_sql,
    ):
        manager.secure_installation()
    run_sql.assert_not_called()


def test_secure_installation_creates_and_grants() -> None:
    manager = _manager("s3cret")
    with (
        patch.object(manager, "has_valid_credentials", return_value=False),
        patch.object(manager, "_run_sql_as_superuser") as run_sql,
    ):
        manager.secure_installation()
    run_sql.assert_called_once()
    sql = run_sql.call_args[0][0]
    assert "CREATE USER IF NOT EXISTS 'root'@'localhost' IDENTIFIED BY 's3cret';" in sql
    assert "ALTER USER 'root'@'localhost' IDENTIFIED BY 's3cret';" in sql
    assert "GRANT ALL PRIVILEGES ON *.* TO 'root'@'localhost' WITH GRANT OPTION;" in sql
    assert "DROP USER IF EXISTS ''@'localhost';" in sql
    assert "DROP DATABASE IF EXISTS test;" in sql
    assert "FLUSH PRIVILEGES;" in sql


def test_secure_installation_escapes_malicious_admin_user() -> None:
    """admin_user is SQL-quoted before secure-installation statements."""
    config = MariaDBConfig(root_password="pw", admin_user="root'; DROP TABLE mysql.user; --")
    manager = MariaDBManager(config)
    with (
        patch.object(manager, "has_valid_credentials", return_value=False),
        patch.object(manager, "_run_sql_as_superuser") as run_sql,
    ):
        manager.secure_installation()
    sql = run_sql.call_args[0][0]
    # The attacker's quote must be escaped, not break out of the string literal.
    assert "root\\'; DROP TABLE mysql.user; --" in sql
    assert "CREATE USER IF NOT EXISTS 'root'" not in sql


def test_run_sql_as_superuser_no_sudo() -> None:
    m = _manager()
    with patch(f"{MODULE}.is_macos", return_value=False), patch(f"{MODULE}.subprocess.run") as run:
        m._run_sql_as_superuser("SELECT 1;")
    cmd = run.call_args[0][0]
    assert "sudo" not in cmd
    assert cmd[0] == "mariadb"


def test_is_reachable_on_macos_ignores_local_socket_path() -> None:
    """socket_path() (our own _STATE_DIR) is never created on macOS - only
    is_running() is a meaningful signal there."""
    m = _manager()
    with (
        patch.object(m, "is_running", return_value=True),
        patch(f"{MODULE}.is_macos", return_value=True),
    ):
        assert m.is_reachable() is True


def test_run_sql_as_superuser_omits_local_socket_on_macos() -> None:
    """Homebrew's mariadb client owns socket resolution on macOS -
    socket_path() (our own _STATE_DIR) is never created there."""
    m = _manager()
    with patch(f"{MODULE}.is_macos", return_value=True), patch(f"{MODULE}.subprocess.run") as run:
        m._run_sql_as_superuser("SELECT 1;")
    cmd = run.call_args[0][0]
    assert cmd == ["mariadb"]


def test_has_valid_credentials_true_on_successful_connect() -> None:
    manager = _manager()
    with patch(f"{MODULE}.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess([], 0)
        assert manager.has_valid_credentials("pw") is True
    # Password is passed via MYSQL_PWD env, never argv.
    args, kwargs = run.call_args
    assert "pw" not in args[0]
    assert kwargs["env"]["MYSQL_PWD"] == "pw"


def test_has_valid_credentials_false_on_error() -> None:
    manager = _manager()
    with patch(f"{MODULE}.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess([], 1)
        assert manager.has_valid_credentials("wrong") is False


def test_has_valid_credentials_times_out() -> None:
    manager = _manager()
    with patch(
        f"{MODULE}.subprocess.run",
        side_effect=subprocess.TimeoutExpired("mariadb", 5),
    ):
        assert manager.has_valid_credentials("wrong") is False


def _client(tmp_path):
    """A signed-in wizard client: the setup routes authenticate like every other route."""
    from admin.backend.app import create_app
    from admin.backend.internal.session import Session
    from pilot.config import BenchConfig
    from pilot.core.bench import Bench

    BenchConfig.write_flat(tmp_path, tmp_path.name, {"admin_enabled": True, "admin_password": "secret"})
    app = create_app(tmp_path)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", Session(Bench(tmp_path)).issue_session_token()[0])
    return client


def _post_validate(client, password: str):
    return client.post(
        "/api/v1/setup/database-validations",
        json={"engine": "mariadb", "password": password},
    )


def test_validate_endpoint_will_install_when_not_installed(tmp_path) -> None:
    with patch(f"{MODULE}.MariaDBManager.is_installed", return_value=False):
        resp = _post_validate(_client(tmp_path), "anything")
    assert resp.get_json() == {"engine": "mariadb", "state": "will_install"}


def test_validate_endpoint_will_install_when_not_provisioned(tmp_path) -> None:
    with (
        patch(f"{MODULE}.MariaDBManager.is_installed", return_value=True),
        patch(f"{MODULE}.MariaDBManager.is_provisioned", return_value=False),
    ):
        resp = _post_validate(_client(tmp_path), "anything")
    assert resp.get_json() == {"engine": "mariadb", "state": "will_install"}


def test_validate_endpoint_valid(tmp_path) -> None:
    with (
        patch(f"{MODULE}.MariaDBManager.is_installed", return_value=True),
        patch(f"{MODULE}.MariaDBManager.is_provisioned", return_value=True),
        patch(f"{MODULE}.MariaDBManager.has_valid_credentials", return_value=True),
    ):
        resp = _post_validate(_client(tmp_path), "correct")
    assert resp.get_json() == {"engine": "mariadb", "state": "valid"}


def test_validate_endpoint_invalid(tmp_path) -> None:
    with (
        patch(f"{MODULE}.MariaDBManager.is_installed", return_value=True),
        patch(f"{MODULE}.MariaDBManager.is_provisioned", return_value=True),
        patch(f"{MODULE}.MariaDBManager.has_valid_credentials", return_value=False),
    ):
        resp = _post_validate(_client(tmp_path), "wrong")
    assert resp.get_json() == {"engine": "mariadb", "state": "invalid"}


def test_validate_endpoint_on_macos_checks_password_for_already_secured_server(tmp_path) -> None:
    """macOS validation checks credentials on an already-secured server."""
    with (
        patch(f"{MODULE}.is_macos", return_value=True),
        patch(f"{MODULE}.MariaDBManager.is_installed", return_value=True),
        patch(f"{MODULE}.MariaDBManager.is_running", return_value=True),
        patch(f"{MODULE}.MariaDBManager.is_unsecured", return_value=False),
        patch(f"{MODULE}.MariaDBManager.has_valid_credentials", return_value=False),
    ):
        resp = _post_validate(_client(tmp_path), "wrong")
    assert resp.get_json() == {"engine": "mariadb", "state": "invalid"}
