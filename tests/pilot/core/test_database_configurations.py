from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from pilot.config import BenchConfig
from pilot.core.database.configurations import DatabaseConfigurations
from pilot.core.database.mariadb_variables import (
    EDITABLE_MARIADB_VARIABLE_NAMES,
    MARIADB_VARIABLE_NAMES,
    MARIADB_VARIABLE_SPECS,
    mariadb_variable_spec,
)
from pilot.core.mariadb_memory import calculate_mariadb_variable_limits
from pilot.exceptions import DatabaseError
from pilot.managers.database.mariadb import MariaDBManager

MODULE = "pilot.core.database.configurations"

PRESS_VARIABLE_NAMES = frozenset(
    {
        "binlog_expire_logs_seconds",
        "binlog_format",
        "connect_timeout",
        "expire_logs_days",
        "extra_max_connections",
        "extra_port",
        "innodb_buffer_pool_size",
        "innodb_force_recovery",
        "innodb_lock_wait_timeout",
        "innodb_log_file_size",
        "innodb_old_blocks_pct",
        "innodb_old_blocks_time",
        "innodb_print_all_deadlocks",
        "innodb_stats_persistent_sample_pages",
        "innodb_status_output_locks",
        "innodb_strict_mode",
        "key_buffer_size",
        "local_infile",
        "log_bin",
        "log_slave_updates",
        "long_query_time",
        "max_allowed_packet",
        "max_connections",
        "max_heap_table_size",
        "max_statement_time",
        "max_user_connections",
        "myisam_recover_options",
        "net_buffer_length",
        "net_read_timeout",
        "net_write_timeout",
        "performance_schema",
        "performance_schema_consumer_events_stages_current",
        "performance_schema_consumer_events_stages_history",
        "performance_schema_consumer_events_stages_history_long",
        "performance_schema_consumer_events_statements_current",
        "performance_schema_consumer_events_statements_history",
        "performance_schema_consumer_events_statements_history_long",
        "performance_schema_consumer_events_waits_current",
        "performance_schema_consumer_events_waits_history",
        "performance_schema_consumer_events_waits_history_long",
        "performance_schema_instrument",
        "read_only",
        "tmp_disk_table_size",
        "tmp_table_size",
        "tmpdir",
        "wait_timeout",
    }
)


def _config(
    *,
    db_type: str = "mariadb",
    existing: bool = False,
    allow_management: bool = True,
) -> BenchConfig:
    config = BenchConfig.default("test")
    config.db_type = db_type
    config.mariadb.existing = existing
    config.admin.allow_bench_management = allow_management
    return config


def _manager(values: dict[str, str] | None = None) -> Mock:
    manager = Mock(spec=MariaDBManager)
    manager.is_installed.return_value = True
    manager.is_provisioned.return_value = True
    manager.is_healthy.return_value = True
    manager.global_variable_values.return_value = values or {}
    manager.performance_schema_enabled.return_value = False
    manager.variable_limits.return_value = calculate_mariadb_variable_limits(2048)
    manager.innodb_buffer_pool_size_mb.return_value = 128
    manager.innodb_buffer_pool_size_max_mb.return_value = 352
    manager.max_connections.return_value = 50
    manager.memory.get_variables.return_value = []
    return manager


def test_memory_limits_reuse_configuration_change_and_task_execution() -> None:
    manager = _manager()
    manager.memory.validate.return_value = {"MemoryHigh": 2500, "MemoryMax": 4500}
    manager.memory.limits = {"MemoryHigh": 2500, "MemoryMax": 3000}
    configurations = DatabaseConfigurations(_config(), manager)
    with patch(f"{MODULE}.is_linux", return_value=True):
        change = configurations.prepare_change("MemoryMax", 4500)
        configurations.set("MemoryMax", 4500)
    assert change == {
        "action": "configuration",
        "name": "MemoryMax",
        "value": 4500,
        "current": 3000,
        "changed": True,
    }
    manager.memory.set.assert_called_once_with("MemoryMax", 4500)


@pytest.mark.parametrize("configuration", [{"existing": True}, {"db_type": "sqlite"}])
def test_memory_changes_respect_configuration_permissions(configuration) -> None:
    manager = _manager()
    with patch(f"{MODULE}.is_linux", return_value=True), pytest.raises(DatabaseError):
        DatabaseConfigurations(_config(**configuration), manager).set("MemoryMax", 4500)
    manager.memory.set.assert_not_called()


def test_memory_changes_do_not_require_sibling_bench_management() -> None:
    manager = _manager()
    with patch(f"{MODULE}.is_linux", return_value=True):
        DatabaseConfigurations(_config(allow_management=False), manager).set("MemoryMax", 4500)
    manager.memory.set.assert_called_once_with("MemoryMax", 4500)


def test_memory_change_requires_an_available_current_limit() -> None:
    manager = _manager()
    manager.memory.validate.return_value = {"MemoryHigh": 2500, "MemoryMax": 4500}
    manager.memory.limits = {"MemoryHigh": 2500, "MemoryMax": None}
    with patch(f"{MODULE}.is_linux", return_value=True), pytest.raises(DatabaseError, match="unavailable"):
        DatabaseConfigurations(_config(), manager).prepare_change("MemoryMax", 4500)


def test_catalog_covers_press_variables_and_pilot_mariadb_118_controls() -> None:
    assert len(MARIADB_VARIABLE_NAMES) == 51
    assert len(MARIADB_VARIABLE_NAMES) == len(MARIADB_VARIABLE_SPECS)
    assert PRESS_VARIABLE_NAMES <= MARIADB_VARIABLE_NAMES
    assert {
        "connect_timeout",
        "innodb_buffer_pool_size",
        "performance_schema",
        "local_infile",
        "innodb_buffer_pool_size_max",
        "innodb_buffer_pool_size_auto_min",
        "innodb_snapshot_isolation",
        "slave_connections_needed_for_purge",
    } <= MARIADB_VARIABLE_NAMES
    assert {
        "connect_timeout",
        "innodb_buffer_pool_size",
        "innodb_lock_wait_timeout",
        "innodb_old_blocks_pct",
        "innodb_old_blocks_time",
        "innodb_print_all_deadlocks",
        "innodb_snapshot_isolation",
        "innodb_stats_persistent_sample_pages",
        "max_connections",
        "net_read_timeout",
        "net_write_timeout",
        "performance_schema",
        "wait_timeout",
    } == EDITABLE_MARIADB_VARIABLE_NAMES


def test_snapshot_exposes_generic_and_guarded_action_variables_as_editable() -> None:
    manager = _manager(
        {
            "connect_timeout": "10",
            "innodb_buffer_pool_size": str(128 * 1024 * 1024),
            "innodb_buffer_pool_size_max": str(352 * 1024 * 1024),
            "innodb_print_all_deadlocks": "ON",
            "max_connections": "50",
            "performance_schema": "OFF",
        }
    )
    with patch(f"{MODULE}.is_linux", return_value=True):
        result = DatabaseConfigurations(_config(), manager).snapshot()

    rows = {row["name"]: row for row in result["variables"]}
    assert result["readable"] is True
    assert result["editable"] is True
    assert rows["connect_timeout"]["value"] == 10
    assert rows["connect_timeout"]["editable"] is True
    assert rows["connect_timeout"]["edit"] == {
        "action": "configuration",
        "value": 10,
        "value_type": "integer",
        "unit": "seconds",
        "min": 2,
        "max": 60,
        "step": 1,
        "recommended": None,
        "dynamic_max": None,
        "requires_restart": False,
    }
    assert rows["innodb_print_all_deadlocks"]["value"] is True
    assert rows["max_connections"]["value"] == 50
    assert rows["max_connections"]["editable"] is True
    assert rows["max_connections"]["edit"] == {
        "action": "max_connections",
        "value": 50,
        "value_type": "integer",
        "unit": "",
        "min": 10,
        "max": 50,
        "step": 1,
        "recommended": 50,
        "dynamic_max": None,
        "requires_restart": False,
    }
    assert rows["innodb_buffer_pool_size"]["editable"] is True
    assert rows["innodb_buffer_pool_size"]["edit"] == {
        "action": "innodb_buffer_pool_size",
        "value": 128,
        "value_type": "integer",
        "unit": "MB",
        "min": 128,
        "max": 352,
        "step": 1,
        "recommended": 128,
        "dynamic_max": 352,
        "requires_restart": False,
    }
    assert rows["performance_schema"]["editable"] is True
    assert rows["performance_schema"]["edit"]["action"] == "performance_schema"
    assert rows["performance_schema"]["edit"]["value"] is False
    assert rows["innodb_buffer_pool_size_max"]["editable"] is False
    assert rows["local_infile"]["supported"] is False
    manager.global_variable_values.assert_called_once()
    manager.performance_schema_enabled.assert_not_called()
    manager.innodb_buffer_pool_size_mb.assert_not_called()
    manager.innodb_buffer_pool_size_max_mb.assert_not_called()
    manager.max_connections.assert_not_called()
    requested = set(manager.global_variable_values.call_args.args[0])
    assert requested == MARIADB_VARIABLE_NAMES


def test_buffer_pool_editor_and_submission_use_the_effective_hard_limit() -> None:
    config = _config()
    service = MariaDBManager(config.mariadb)
    manager = _manager(
        {
            "innodb_buffer_pool_size": str(768 * 1024 * 1024),
            "innodb_buffer_pool_size_max": str(2048 * 1024 * 1024),
        }
    )
    manager.variable_limits.side_effect = service.variable_limits
    configurations = DatabaseConfigurations(config, manager)
    with (
        patch(f"{MODULE}.is_linux", return_value=True),
        patch("pilot.core.database.quick_actions.is_linux", return_value=True),
        patch("pilot.managers.database.mariadb.is_macos", return_value=False),
        patch.object(service, "_total_memory_mb", return_value=8192),
        patch.object(service, "_unit_memory_mb", return_value=1024),
    ):
        rows = {row["name"]: row for row in configurations.snapshot()["variables"]}
        assert rows["innodb_buffer_pool_size"]["edit"]["max"] == 1023
        with pytest.raises(ValueError, match="1023"):
            configurations.prepare_change("innodb_buffer_pool_size", 1024)


def test_buffer_pool_editor_requires_the_mariadb_118_live_ceiling() -> None:
    manager = _manager({"innodb_buffer_pool_size": str(128 * 1024 * 1024)})

    with patch(f"{MODULE}.is_linux", return_value=True):
        result = DatabaseConfigurations(_config(), manager).snapshot()

    row = next(variable for variable in result["variables"] if variable["name"] == "innodb_buffer_pool_size")
    assert row["editable"] is False
    assert row["edit"] is None
    assert "does not expose the live Buffer Pool ceiling" in row["reason"]


def test_edits_do_not_depend_on_bench_management() -> None:
    manager = _manager({"connect_timeout": "10", "max_connections": "50"})
    with patch(f"{MODULE}.is_linux", return_value=True):
        result = DatabaseConfigurations(_config(allow_management=False), manager).snapshot()

    rows = {row["name"]: row for row in result["variables"]}
    assert result["editable"] is True
    assert rows["connect_timeout"]["editable"] is True
    assert rows["max_connections"]["editable"] is True


def test_external_mariadb_values_are_readable_but_all_edits_are_disabled() -> None:
    manager = _manager({"connect_timeout": "10"})
    result = DatabaseConfigurations(_config(existing=True), manager).snapshot()

    assert result["managed"] is False
    assert result["readable"] is True
    assert result["editable"] is False
    assert "External MariaDB configurations are read-only" in result["edit_reason"]
    assert all(row["editable"] is False for row in result["variables"])
    manager.is_installed.assert_not_called()
    manager.is_provisioned.assert_not_called()


def test_non_mariadb_snapshot_returns_capability_response_without_manager() -> None:
    result = DatabaseConfigurations(_config(db_type="sqlite")).snapshot()

    assert result == {
        "engine": "sqlite",
        "managed": False,
        "readable": False,
        "editable": False,
        "reason": "Database configurations are available only when the bench uses MariaDB.",
        "edit_reason": "Database configurations are available only when the bench uses MariaDB.",
        "variables": [],
    }


def test_unprovisioned_server_is_not_probed() -> None:
    manager = _manager()
    manager.is_provisioned.return_value = False

    result = DatabaseConfigurations(_config(), manager).snapshot()

    assert result["readable"] is False
    assert "has not been provisioned" in result["reason"]
    manager.is_healthy.assert_not_called()
    manager.global_variable_values.assert_not_called()


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("connect_timeout", True, "whole number"),
        ("connect_timeout", "10", "whole number"),
        ("connect_timeout", 1, "at least 2"),
        ("connect_timeout", 61, "greater than 60"),
        ("innodb_print_all_deadlocks", 1, "enabled or disabled"),
        ("innodb_print_all_deadlocks", "ON", "enabled or disabled"),
        ("innodb_buffer_pool_size_max", 256, "read-only"),
        ("unknown_variable", 1, "not supported"),
    ],
)
def test_catalog_rejects_wrong_types_ranges_and_read_only_variables(
    name: str,
    value,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        mariadb_variable_spec(name).validate_input(value)


def test_guarded_action_variables_cannot_reach_the_generic_manager_path() -> None:
    with pytest.raises(ValueError, match="guarded database action"):
        mariadb_variable_spec("max_connections").validate_input(40)


def test_prepare_change_normalizes_live_value_and_detects_noop() -> None:
    manager = _manager({"connect_timeout": "10"})
    configurations = DatabaseConfigurations(_config(), manager)

    with patch(f"{MODULE}.is_linux", return_value=True):
        change = configurations.prepare_change("connect_timeout", 10)

    assert change == {
        "action": "configuration",
        "name": "connect_timeout",
        "value": 10,
        "current": 10,
        "changed": False,
    }


@pytest.mark.parametrize(
    ("name", "value", "current", "action"),
    [
        ("max_connections", 40, 50, "max_connections"),
        ("innodb_buffer_pool_size", 256, 128, "innodb_buffer_pool_size"),
        ("performance_schema", True, False, "performance_schema"),
    ],
)
def test_prepare_change_routes_guarded_variables_through_quick_action_policy(
    name: str,
    value,
    current,
    action: str,
) -> None:
    manager = _manager()
    configurations = DatabaseConfigurations(_config(), manager)

    with (
        patch(f"{MODULE}.is_linux", return_value=True),
        patch("pilot.core.database.quick_actions.is_linux", return_value=True),
    ):
        change = configurations.prepare_change(name, value)

    assert change == {
        "action": action,
        "name": name,
        "value": value,
        "current": current,
        "changed": True,
    }


def test_prepare_change_rejects_external_server_before_queueing() -> None:
    manager = _manager({"connect_timeout": "10"})

    with pytest.raises(DatabaseError, match="External MariaDB configurations are read-only"):
        DatabaseConfigurations(_config(existing=True), manager).prepare_change(
            "connect_timeout",
            20,
        )


def test_set_revalidates_policy_and_forwards_normalized_value() -> None:
    manager = _manager()
    manager.set_configuration_variable.return_value = True
    configurations = DatabaseConfigurations(_config(), manager)

    with patch(f"{MODULE}.is_linux", return_value=True):
        assert configurations.set("innodb_print_all_deadlocks", False) is True

    manager.set_configuration_variable.assert_called_once_with(
        "innodb_print_all_deadlocks",
        False,
    )
