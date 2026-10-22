from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch

import pytest

from pilot.config import BenchConfig
from pilot.core.database.quick_actions import DatabaseQuickActions
from pilot.core.mariadb_memory import calculate_mariadb_variable_limits
from pilot.exceptions import DatabaseError
from pilot.managers.database.mariadb import MariaDBManager

MODULE = "pilot.core.database.quick_actions"


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


def _manager(
    *,
    installed: bool = True,
    provisioned: bool = True,
    healthy: bool = True,
    performance_schema: bool = False,
) -> Mock:
    manager = Mock(spec=MariaDBManager)
    manager.is_installed.return_value = installed
    manager.is_provisioned.return_value = provisioned
    manager.is_healthy.return_value = healthy
    manager.performance_schema_enabled.return_value = performance_schema
    manager.variable_limits.return_value = calculate_mariadb_variable_limits(2048)
    manager.innodb_buffer_pool_size_mb.return_value = 128
    manager.innodb_buffer_pool_size_max_mb.return_value = 352
    manager.max_connections.return_value = 50
    manager.host_memory_mb = 4096
    return manager


@pytest.mark.parametrize(
    ("db_type", "reason"),
    [
        ("sqlite", "SQLite does not have a shared database server."),
        ("postgres", "only when the bench uses MariaDB"),
    ],
)
def test_database_section_capabilities_are_returned_for_every_engine(
    db_type: str,
    reason: str,
) -> None:
    result = DatabaseQuickActions(_config(db_type=db_type)).capabilities()

    assert result["engine"] == db_type
    assert result["managed"] is False
    assert set(result["actions"]) == {
        "restart",
        "performance_schema",
        "innodb_buffer_pool_size",
        "max_connections",
        "memory_share",
        "manage_binlogs",
    }
    assert all(action["available"] is False for action in result["actions"].values())
    assert reason in result["actions"]["restart"]["reason"]


def test_managed_mariadb_exposes_all_applicable_actions() -> None:
    manager = _manager(performance_schema=True)
    with patch(f"{MODULE}.is_linux", return_value=True):
        result = DatabaseQuickActions(_config(), manager).capabilities()

    assert result["managed"] is True
    assert result["reachable"] is True
    assert result["actions"]["restart"]["available"] is True
    assert result["actions"]["performance_schema"] == {
        "available": True,
        "reason": "",
        "enabled": True,
        "requires_restart": True,
    }
    assert result["actions"]["innodb_buffer_pool_size"] == {
        "available": True,
        "reason": "",
        "current_mb": 128,
        "min_mb": 128,
        "max_mb": 352,
        "recommended_mb": 128,
        "dynamic_max_mb": 352,
        "unit": "MB",
        "requires_restart": False,
    }
    assert result["actions"]["max_connections"] == {
        "available": True,
        "reason": "",
        "current": 50,
        "min": 10,
        "max": 50,
        "recommended": 50,
        "requires_restart": False,
    }
    assert result["actions"]["manage_binlogs"]["available"] is True


def test_external_mariadb_disables_every_database_action_without_probing_server() -> None:
    manager = _manager()
    result = DatabaseQuickActions(_config(existing=True), manager).capabilities()

    assert result["managed"] is False
    assert all(action["available"] is False for action in result["actions"].values())
    assert all("Pilot-managed MariaDB" in action["reason"] for action in result["actions"].values())
    manager.is_installed.assert_not_called()
    manager.is_healthy.assert_not_called()
    manager.performance_schema_enabled.assert_not_called()


def test_external_mariadb_restart_is_rejected_before_manager_call() -> None:
    manager = _manager()
    actions = DatabaseQuickActions(_config(existing=True), manager)

    with pytest.raises(DatabaseError, match="external MariaDB"):
        actions.restart()

    manager.restart_managed_server.assert_not_called()


def test_actions_do_not_depend_on_bench_management() -> None:
    manager = _manager()
    with patch(f"{MODULE}.is_linux", return_value=True):
        result = DatabaseQuickActions(
            _config(allow_management=False),
            manager,
        ).capabilities()

    assert all(action["available"] for action in result["actions"].values())


def test_unreachable_mariadb_can_be_restarted_but_cannot_be_configured() -> None:
    manager = _manager(healthy=False)
    with patch(f"{MODULE}.is_linux", return_value=True):
        result = DatabaseQuickActions(_config(), manager).capabilities()

    assert result["actions"]["restart"]["available"] is True
    assert result["actions"]["performance_schema"]["available"] is False
    assert result["actions"]["innodb_buffer_pool_size"]["available"] is False
    assert result["actions"]["max_connections"]["available"] is False
    assert result["actions"]["manage_binlogs"]["available"] is False
    assert "not reachable" in result["actions"]["performance_schema"]["reason"]


def test_unprovisioned_managed_server_does_not_attempt_database_connection() -> None:
    manager = _manager(provisioned=False)
    result = DatabaseQuickActions(_config(), manager).capabilities()

    assert result["reachable"] is False
    assert result["actions"]["restart"]["available"] is False
    assert "has not been provisioned" in result["actions"]["manage_binlogs"]["reason"]
    manager.is_healthy.assert_not_called()


def test_restart_revalidates_capability_before_calling_manager() -> None:
    manager = _manager()
    actions = DatabaseQuickActions(_config(), manager)

    actions.restart()

    manager.restart_managed_server.assert_called_once()


def test_performance_schema_revalidates_capability_and_passes_boolean() -> None:
    manager = _manager(performance_schema=False)
    manager.set_performance_schema.return_value = True
    actions = DatabaseQuickActions(_config(), manager)

    with patch(f"{MODULE}.is_linux", return_value=True):
        assert actions.set_performance_schema(True) is True

    manager.set_performance_schema.assert_called_once_with(True, restart_executor=None)


def test_performance_schema_forwards_restart_executor() -> None:
    manager = _manager(performance_schema=False)
    restart_executor = Mock()
    actions = DatabaseQuickActions(_config(), manager)

    with patch(f"{MODULE}.is_linux", return_value=True):
        actions.set_performance_schema(True, restart_executor=restart_executor)

    manager.set_performance_schema.assert_called_once_with(
        True,
        restart_executor=restart_executor,
    )


def test_performance_schema_rejects_non_boolean() -> None:
    manager = _manager()
    with pytest.raises(DatabaseError, match="enabled must be a boolean"):
        DatabaseQuickActions(_config(), manager).set_performance_schema(1)
    manager.set_performance_schema.assert_not_called()


def test_sizing_actions_revalidate_ranges_and_call_manager() -> None:
    manager = _manager()
    manager.set_innodb_buffer_pool_size.return_value = True
    manager.set_max_connections.return_value = True
    actions = DatabaseQuickActions(_config(), manager)

    with patch(f"{MODULE}.is_linux", return_value=True):
        assert actions.set_innodb_buffer_pool_size(256) is True
        assert actions.set_max_connections(40) is True

    manager.set_innodb_buffer_pool_size.assert_called_once_with(256)
    manager.set_max_connections.assert_called_once_with(40)


@pytest.mark.parametrize(
    ("method", "value", "message"),
    [
        ("require_innodb_buffer_pool_size", 127, "between 128 and 352"),
        ("require_innodb_buffer_pool_size", 353, "between 128 and 352"),
        ("require_max_connections", 9, "between 10 and 50"),
        ("require_max_connections", 51, "between 10 and 50"),
        ("require_max_connections", True, "whole number"),
    ],
)
def test_sizing_actions_reject_invalid_values(
    method: str,
    value,
    message: str,
) -> None:
    manager = _manager()
    actions = DatabaseQuickActions(_config(), manager)

    with (
        patch(f"{MODULE}.is_linux", return_value=True),
        pytest.raises(ValueError, match=message),
    ):
        getattr(actions, method)(value)


def test_the_memory_split_previews_the_sizing_of_each_share() -> None:
    with patch(f"{MODULE}.is_linux", return_value=True):
        capability = DatabaseQuickActions(_config(), _manager()).capabilities()["actions"]["memory_share"]

    assert (capability["current"], capability["min"], capability["max"]) == (0.5, 0.2, 0.8)
    assert capability["total_memory_mb"] == 4096
    shares = [step["share"] for step in capability["steps"]]
    assert shares[0] == 0.2 and shares[-1] == 0.8 and 0.5 in shares
    pools = [step["innodb_buffer_pool_mb"] for step in capability["steps"]]
    assert pools == sorted(pools)


@pytest.mark.parametrize("share", [0.1, 0.9, "0.5"])
def test_a_memory_share_outside_the_range_is_refused(share) -> None:
    with (
        patch(f"{MODULE}.is_linux", return_value=True),
        pytest.raises(ValueError, match=r"between 0\.2 and 0\.8"),
    ):
        DatabaseQuickActions(_config(), _manager()).require_memory_share(share)


def test_setting_the_memory_share_saves_it_for_every_bench_and_sizes_mariadb(tmp_path) -> None:
    from pilot.config.common import CommonConfig

    manager = _manager()
    manager.database_action_lock.return_value = MagicMock()
    actions = DatabaseQuickActions(_config(), manager)
    with CommonConfig.open(tmp_path) as common:
        common.mariadb.memory_high_mb = 1200
        common.mariadb.memory_max_mb = 1600
    actions.config.mariadb.memory_high_mb = 1200
    actions.config.mariadb.memory_max_mb = 1600
    with patch(f"{MODULE}.is_linux", return_value=True):
        actions.set_memory_share(0.65, tmp_path)

    assert CommonConfig.read(tmp_path).mariadb.memory_share == 0.65
    assert actions.config.mariadb.memory_share == 0.65
    assert CommonConfig.read(tmp_path).mariadb.memory_high_mb is None
    assert CommonConfig.read(tmp_path).mariadb.memory_max_mb is None
    assert actions.config.mariadb.memory_high_mb is None
    assert actions.config.mariadb.memory_max_mb is None
    manager.tune_to_host_locked.assert_called_once()


def test_a_memory_share_that_fails_to_apply_is_not_saved(tmp_path) -> None:
    """Another bench can hold the database lock; the saved share must match the server."""
    from pilot.config.common import CommonConfig
    from pilot.exceptions import DatabaseError

    manager = _manager()
    manager.database_action_lock.side_effect = DatabaseError("Another database action is already running.")
    actions = DatabaseQuickActions(_config(), manager)
    with patch(f"{MODULE}.is_linux", return_value=True), pytest.raises(DatabaseError):
        actions.set_memory_share(0.65, tmp_path)

    assert CommonConfig.read(tmp_path).mariadb.memory_share != 0.65
    manager.tune_to_host_locked.assert_not_called()
