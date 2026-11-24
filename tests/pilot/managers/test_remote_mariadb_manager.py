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


def test_the_server_and_its_agent_are_reached_at_the_configured_host() -> None:
    manager = RemoteMariaDBManager(_config(agent_port=7790))

    assert manager.site_host == "fdaa:1:2::3"
    assert manager.client_socket == ""
    assert manager._client_command()[-4:] == ["-h", "fdaa:1:2::3", "-P", "3306"]
    assert (manager.agent.base_url, manager.agent.token) == ("http://[fdaa:1:2::3]:7790", "token")


@pytest.mark.parametrize(
    ("method", "arguments", "message"),
    [
        pytest.param("_write_config", (), "sizes its own", id="server-options"),
        pytest.param("_run_sql_as_superuser", ("SELECT 1;",), "no superuser login", id="superuser-sql"),
    ],
)
def test_this_host_does_nothing_only_the_database_vm_may_do(
    method: str, arguments: tuple, message: str
) -> None:
    manager, _ = _manager()

    with pytest.raises(DatabaseError, match=message):
        getattr(manager, method)(*arguments)


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


@pytest.mark.parametrize(
    ("logins", "calls"),
    [
        pytest.param([False, True], [("grant_admin", "pilot", "secret")], id="new-admin"),
        pytest.param([True, True], [], id="admin-already-logs-in"),
    ],
)
def test_provision_only_grants_an_admin_for_this_host_through_the_agent(logins: list, calls: list) -> None:
    manager, agent = _manager()

    with (
        patch.object(RemoteMariaDBManager, "has_valid_credentials", side_effect=logins),
        patch.object(RemoteMariaDBManager, "is_healthy", side_effect=AssertionError("needs PyMySQL")),
    ):
        manager.provision()

    # The database VM sized and started its own server, so provisioning only adds the account.
    assert agent.calls == calls


@pytest.mark.parametrize(
    ("config", "health", "message"),
    [
        pytest.param({"root_password": ""}, {}, "root_password", id="no-admin-password"),
        pytest.param({}, {"is_provisioned": False}, "pilot setup database-server", id="not-set-up"),
        pytest.param({}, {"version": [10, 11]}, "11.8 or newer.*runs 10.11", id="old-server"),
    ],
)
def test_provision_refuses_a_database_vm_it_cannot_take_over(
    config: dict, health: dict, message: str
) -> None:
    manager, agent = _manager(**config)
    agent.health.update(health)

    with pytest.raises(DatabaseError, match=message):
        manager.provision()
    assert agent.calls == []


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
