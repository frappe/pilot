from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from pilot.config import MariaDBConfig
from pilot.core.database.agent.client import API_VERSION
from pilot.exceptions import DatabaseError
from pilot.managers.database.mariadb import MariaDBManager
from pilot.managers.database.remote_mariadb import RemoteMariaDBManager


class FakeAgent:
    """A database agent whose server state lives in attributes and whose calls are recorded."""

    def __init__(self) -> None:
        self.health: dict[str, Any] = {
            "api_version": API_VERSION,
            "is_installed": True,
            "is_provisioned": True,
            "is_running": True,
            "version": [11, 8],
            "memory_mb": 8192,
            "client_address": "fdaa:1:2::7",
        }
        self.managed_config = "# Managed by Pilot's database variable editor.\n[mysqld]\n"
        self.calls: list[tuple] = []

    def get_health(self) -> dict[str, Any]:
        return self.health

    def read_managed_config(self) -> str:
        return self.managed_config

    def write_managed_config(self, content: str) -> None:
        self.calls.append(("write_managed_config",))
        self.managed_config = content

    def control(self, action: str, timeout: float) -> None:
        self.calls.append(("control", action, timeout))

    def grant_admin(self, user: str, password: str) -> str:
        self.calls.append(("grant_admin", user, password))
        return self.health["client_address"]


def _config(**changes) -> MariaDBConfig:
    values = dict(
        host="fdaa:1:2::3",
        port=3306,
        remote=True,
        agent_token="token",
        admin_user="pilot",
        root_password="secret",
    )
    return MariaDBConfig(**{**values, **changes})


def _manager(**changes) -> tuple[RemoteMariaDBManager, FakeAgent]:
    manager = MariaDBManager.for_config(_config(**changes))
    assert isinstance(manager, RemoteMariaDBManager)
    agent = FakeAgent()
    manager.__dict__["agent"] = agent
    return manager, agent


def test_a_remote_config_gets_the_remote_manager_on_another_host() -> None:
    manager, _ = _manager()

    assert manager.is_on_another_host is True
    assert manager.site_host == "fdaa:1:2::3"
    assert manager.client_socket == ""
    assert manager._client_command()[-4:] == ["-h", "fdaa:1:2::3", "-P", "3306"]


def test_the_agent_client_uses_the_configured_host_port_and_token() -> None:
    agent = RemoteMariaDBManager(_config(agent_port=7790)).agent

    assert (agent.base_url, agent.token) == ("http://[fdaa:1:2::3]:7790", "token")


def test_this_host_never_writes_the_servers_options() -> None:
    manager, _ = _manager()

    with pytest.raises(DatabaseError, match="sizes its own"):
        manager._write_config()


def test_managed_options_are_read_and_written_through_the_agent() -> None:
    manager, agent = _manager()

    manager.ensure_managed_config()
    manager._write_managed_options({"max-connections": "300"})

    assert manager._read_managed_options() == {"max-connections": "300"}
    assert "max-connections = 300" in agent.managed_config


def test_variable_limits_come_from_the_database_vm_memory_without_a_service_cap() -> None:
    manager, agent = _manager()
    agent.health["memory_mb"] = 16384

    with patch("pilot.managers.database.mariadb.run_command") as run_command:
        limits = manager.variable_limits()

    run_command.assert_not_called()
    assert limits.innodb_buffer_pool_max_mb > 8192


def test_restart_runs_through_the_agent() -> None:
    manager, agent = _manager()

    with patch.object(RemoteMariaDBManager, "is_healthy", return_value=True):
        manager._restart_and_wait_healthy()

    assert agent.calls == [("control", "restart", RemoteMariaDBManager.service_timeout)]


def test_provision_creates_an_admin_for_this_host_through_the_agent() -> None:
    manager, agent = _manager()

    with (
        patch.object(RemoteMariaDBManager, "has_valid_credentials", side_effect=[False, True]),
        patch.object(RemoteMariaDBManager, "is_healthy", side_effect=AssertionError("needs PyMySQL")),
    ):
        manager.provision()

    # The database VM sized and started its own server, so provisioning only adds the account.
    assert agent.calls == [("grant_admin", "pilot", "secret")]


def test_provision_keeps_an_admin_that_already_logs_in() -> None:
    manager, agent = _manager()

    with patch.object(RemoteMariaDBManager, "has_valid_credentials", return_value=True):
        manager.provision()

    assert agent.calls == []


@pytest.mark.parametrize(
    ("health", "message"),
    [
        ({"is_provisioned": False}, "pilot setup database-server"),
        ({"version": [10, 11]}, "11.8 or newer.*runs 10.11"),
    ],
)
def test_provision_refuses_a_database_vm_that_is_not_ready(health: dict, message: str) -> None:
    manager, agent = _manager()
    agent.health.update(health)

    with pytest.raises(DatabaseError, match=message):
        manager.provision()
    assert agent.calls == []


def test_provision_needs_an_admin_password() -> None:
    manager, agent = _manager(root_password="")

    with pytest.raises(DatabaseError, match="root_password"):
        manager.provision()
    assert agent.calls == []


def test_this_host_has_no_superuser_login() -> None:
    manager, _ = _manager()

    with pytest.raises(DatabaseError, match="no superuser login"):
        manager._run_sql_as_superuser("SELECT 1;")


def test_setup_accounts_name_the_host_the_admin_connects_from() -> None:
    manager, _ = _manager()
    statements: list[str] = []

    def run_admin_sql(self, sql: str) -> str:
        statements.append(sql)
        return "SUBSTRING_INDEX(USER(), '@', -1)\nfdaa:1:2::7\n" if sql.startswith("SELECT") else ""

    with (
        patch.object(RemoteMariaDBManager, "run_admin_sql", run_admin_sql),
        manager.temporary_setup_user("_abc") as (user, _),
    ):
        pass

    # The CLI's Python has no MySQL driver, so the host is read with the client.
    assert statements[0] == "SELECT SUBSTRING_INDEX(USER(), '@', -1);"
    assert f"CREATE USER '{user}'@'fdaa:1:2::7'" in statements[1]
    assert f"DROP USER IF EXISTS '{user}'@'fdaa:1:2::7'" in statements[-1]


def test_configuration_changes_need_no_local_linux() -> None:
    manager, _ = _manager()

    with patch("pilot.managers.database.mariadb.HostPlatform") as platform:
        platform.return_value.is_macos = True
        manager._require_linux_managed_server()


def test_a_remote_server_is_ready_when_the_admin_can_query_it() -> None:
    manager, _ = _manager()

    with patch.object(RemoteMariaDBManager, "has_valid_credentials", return_value=False):
        assert manager.is_reachable() is False
