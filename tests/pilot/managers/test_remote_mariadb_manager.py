from __future__ import annotations

from unittest.mock import patch

import pytest

from pilot.config import MariaDBConfig
from pilot.core.mariadb_memory import calculate_mariadb_memory
from pilot.exceptions import DatabaseError
from pilot.managers.database.mariadb import MariaDBManager
from pilot.managers.database.remote_mariadb import RemoteMariaDBManager

PILOT_CNF = "/etc/mysql/mariadb.conf.d/90-pilot.cnf"
MANAGED_CNF = "/etc/mysql/mariadb.conf.d/99-pilot-managed.cnf"


class FakeSSHHost:
    """An SSH host whose files live in a dict and whose commands are recorded."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self.files = {"/usr/sbin/mariadbd": "", **(files or {})}
        self.commands: list[list[str]] = []
        self.stdin: list[str] = []
        self.outputs: dict[str, str] = {
            "/usr/sbin/mariadbd --version": "/usr/sbin/mariadbd  Ver 11.8.3-MariaDB-ubu2404 for debian-linux-gnu"
        }
        self.reachable = True

    def is_reachable(self) -> bool:
        return self.reachable

    def exists(self, path: str) -> bool:
        return path in self.files

    def read_text(self, path: str) -> str:
        return self.files[path]

    def write_text(self, path: str, text: str, mode: str = "0644") -> None:
        self.commands.append(["write", path])
        self.files[path] = text

    def run(self, argv: list[str], *, sudo: bool = True, stdin_text: str | None = None, timeout=None) -> str:
        self.commands.append(argv)
        if stdin_text is not None:
            self.stdin.append(stdin_text)
        return self.outputs.get(" ".join(argv), "")

    def get_client_address(self) -> str:
        return "fdaa:1:2::7"


def _config(**changes) -> MariaDBConfig:
    values = dict(
        host="fdaa:1:2::3",
        port=3306,
        remote=True,
        ssh_user="ubuntu",
        ssh_key_path="/home/pilot/.ssh/database",
        admin_user="pilot",
        root_password="secret",
        memory_mb=8192,
    )
    return MariaDBConfig(**{**values, **changes})


def _manager(host: FakeSSHHost | None = None, **changes) -> tuple[RemoteMariaDBManager, FakeSSHHost]:
    manager = MariaDBManager.for_config(_config(**changes))
    assert isinstance(manager, RemoteMariaDBManager)
    host = host or FakeSSHHost()
    manager.__dict__["ssh_host"] = host
    return manager, host


def test_a_remote_config_gets_the_remote_manager_on_another_host() -> None:
    manager, _ = _manager()

    assert manager.is_on_another_host is True
    assert manager.site_host == "fdaa:1:2::3"
    assert manager.client_socket == ""
    assert manager._client_command()[-4:] == ["-h", "fdaa:1:2::3", "-P", "3306"]


def test_the_ssh_host_uses_the_configured_login() -> None:
    host = RemoteMariaDBManager(_config()).ssh_host

    assert (host.address, host.user, str(host.key_path)) == (
        "fdaa:1:2::3",
        "ubuntu",
        "/home/pilot/.ssh/database",
    )


def test_options_go_to_the_package_conf_d_sized_for_the_whole_vm() -> None:
    manager, host = _manager(memory_mb=16384)

    sizing = manager._write_config()

    # The VM runs nothing else, so the server is sized for all of the memory Central gave it.
    assert sizing == calculate_mariadb_memory(16384, memory_share=1.0)
    content = host.files[PILOT_CNF]
    assert "# pilot-sized-for-memory-mb = 16384" in content
    assert "bind-address = ::\n" in content
    assert "skip-name-resolve = ON\n" in content
    assert "port = 3306\n" in content
    assert f"innodb-buffer-pool-size = {sizing.innodb_buffer_pool_mb}M\n" in content
    assert "log-error = /var/log/mysql/error.log\n" in content
    # The package keeps its own data directory, socket and client settings.
    assert "datadir" not in content and "socket" not in content and "[client]" not in content
    assert host.files[MANAGED_CNF].startswith("# Managed by Pilot's database variable editor.")


def test_managed_options_are_read_and_written_on_the_remote_host() -> None:
    manager, host = _manager()

    manager.ensure_managed_config()
    manager._write_managed_options({"max-connections": "300"})

    assert manager._read_managed_options() == {"max-connections": "300"}
    assert "max-connections = 300" in host.files[MANAGED_CNF]


def test_variable_limits_come_from_the_vm_memory_without_a_service_cap() -> None:
    manager, host = _manager()

    with patch("pilot.managers.database.mariadb.run_command") as run_command:
        limits = manager.variable_limits()

    run_command.assert_not_called()
    assert limits.innodb_buffer_pool_max_mb > 4096
    assert host.commands == []


def test_restart_runs_on_the_remote_service() -> None:
    manager, host = _manager()

    with patch.object(RemoteMariaDBManager, "is_healthy", return_value=True):
        manager._restart_and_wait_healthy()

    assert host.commands[-1] == ["systemctl", "restart", "mariadb.service"]


def test_provision_writes_options_restarts_and_creates_an_admin_for_this_host_only() -> None:
    manager, host = _manager()

    with (
        patch.object(RemoteMariaDBManager, "has_valid_credentials", side_effect=[False, True]),
        patch.object(RemoteMariaDBManager, "is_healthy", side_effect=AssertionError("needs PyMySQL")),
    ):
        manager.provision()

    written = [command[1] for command in host.commands if command[0] == "write"]
    assert host.commands[0] == ["/usr/sbin/mariadbd", "--version"]
    assert written == [MANAGED_CNF, PILOT_CNF]
    restart = host.commands.index(["systemctl", "restart", "mariadb.service"])
    assert host.commands.index(["mariadb"]) > restart
    # The server has its VM to itself, so its service gets no memory limits.
    assert not any("set-property" in command for command in host.commands)
    assert "CREATE USER IF NOT EXISTS 'pilot'@'fdaa:1:2::7' IDENTIFIED BY 'secret';" in host.stdin[0]
    assert "GRANT ALL PRIVILEGES ON *.* TO 'pilot'@'fdaa:1:2::7' WITH GRANT OPTION;" in host.stdin[0]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda host: setattr(host, "reachable", False), "cannot log in"),
        (lambda host: host.files.pop("/usr/sbin/mariadbd"), "not installed"),
    ],
)
def test_provision_fails_before_touching_an_unusable_host(change, message: str) -> None:
    manager, host = _manager()
    change(host)

    with pytest.raises(DatabaseError, match=message):
        manager.provision()
    assert host.commands == []


def test_provision_needs_an_admin_password() -> None:
    manager, host = _manager(root_password="")

    with pytest.raises(DatabaseError, match="root_password"):
        manager.provision()
    assert host.commands == []


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
    manager, host = _manager(FakeSSHHost(files={PILOT_CNF: ""}))

    with patch("pilot.managers.database.mariadb.HostPlatform") as platform:
        platform.return_value.is_macos = True
        manager._require_linux_managed_server()


def test_a_remote_server_is_ready_when_the_admin_can_query_it() -> None:
    manager, host = _manager()

    with patch.object(RemoteMariaDBManager, "has_valid_credentials", return_value=False):
        assert manager.is_reachable() is False
    assert host.commands == []


def test_provision_refuses_a_server_older_than_pilot_supports() -> None:
    host = FakeSSHHost()
    host.outputs["/usr/sbin/mariadbd --version"] = "/usr/sbin/mariadbd  Ver 10.11.13-MariaDB-0ubuntu0.24.04.1"
    manager, _ = _manager(host)

    with pytest.raises(DatabaseError, match="11.8 or newer.*runs 10.11"):
        manager.provision()
    assert [command for command in host.commands if command[0] == "write"] == []
