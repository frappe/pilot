from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from pilot.config import MariaDBConfig
from pilot.config.common import CommonConfig
from pilot.config.database_agent import DatabaseAgentConfig
from pilot.core.database.agent.app import create_app
from pilot.core.database.agent.client import API_VERSION
from pilot.managers.database.dedicated_mariadb import DedicatedMariaDBManager

APP_VM = "fdaa:1:2::7"
TOKEN = "token"


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


def _client(address: str = APP_VM, token: str = TOKEN) -> TestClient:
    config = CommonConfig(
        mariadb=MariaDBConfig(port=3306, dedicated=True),
        database_agent=DatabaseAgentConfig(
            token_hash=hashlib.sha256(TOKEN.encode()).hexdigest(), client_address=APP_VM
        ),
    )
    return TestClient(
        create_app(config), client=(address, 50000), headers={"Authorization": f"Bearer {token}"}
    )


@pytest.mark.parametrize(
    ("address", "token", "status"),
    [(APP_VM, "wrong", 401), ("fdaa:1:2::8", TOKEN, 403), ("testclient", TOKEN, 403)],
)
def test_only_the_app_vm_with_the_token_is_served(manager, address: str, token: str, status: int) -> None:
    with patch.object(DedicatedMariaDBManager, "control") as control:
        response = _client(address, token).post("/service/restart")

    assert response.status_code == status
    control.assert_not_called()


def test_the_app_vm_address_matches_however_it_is_written(manager) -> None:
    with patch.object(DedicatedMariaDBManager, "control"):
        assert _client("fdaa:1:2:0::7").post("/service/restart").status_code == 200


def test_health_reports_the_server_and_the_callers_address(manager) -> None:
    with (
        patch.object(DedicatedMariaDBManager, "is_installed", return_value=True),
        patch.object(DedicatedMariaDBManager, "is_provisioned", return_value=True),
        patch.object(DedicatedMariaDBManager, "is_running", return_value=True),
        patch.object(DedicatedMariaDBManager, "get_server_version", return_value=(11, 8)),
        patch.object(DedicatedMariaDBManager, "_total_memory_mb", return_value=8192),
    ):
        health = _client().get("/health").json()

    assert health == {
        "api_version": API_VERSION,
        "is_installed": True,
        "is_provisioned": True,
        "is_running": True,
        "version": [11, 8],
        "memory_mb": 8192,
        "client_address": APP_VM,
    }


def test_the_managed_config_takes_only_options_pilot_manages(manager) -> None:
    client = _client()

    accepted = client.put("/config/managed", json={"content": "[mysqld]\nmax-connections = 300\n"})
    refused = client.put("/config/managed", json={"content": "[mysqld]\ninit-file = /tmp/x.sql\n"})

    assert accepted.status_code == 200
    assert refused.status_code == 422
    assert "init-file" in refused.json()["detail"]
    assert client.get("/config/managed").json() == {"content": "[mysqld]\nmax-connections = 300\n"}


def test_a_service_action_outside_the_list_is_refused(manager) -> None:
    with patch.object(DedicatedMariaDBManager, "control") as control:
        assert _client().post("/service/disable").status_code == 422
    control.assert_not_called()


def test_server_files_are_read_and_other_files_refused(manager) -> None:
    manager.error_log_path.write_text("first\nlast\n")
    client = _client()

    log = client.get("/files", params={"path": str(manager.error_log_path), "tail_bytes": 5}).json()
    missing = client.get("/files", params={"path": str(manager.data_dir / "mysql-bin.index")}).json()
    refused = client.get("/files", params={"path": str(manager.data_dir / ".." / ".." / "config" / "my.cnf")})

    assert log == {"exists": True, "size_bytes": 11, "text": "last\n"}
    assert missing == {"exists": False, "size_bytes": 0, "text": ""}
    assert refused.status_code == 422


def test_the_admin_account_is_granted_to_the_callers_address(manager) -> None:
    with patch.object(DedicatedMariaDBManager, "_run_sql_as_superuser") as run_sql:
        response = _client().post("/accounts/admin", json={"user": "pilot", "password": "secret"})

    assert response.json() == {"host": APP_VM}
    assert (
        f"CREATE USER IF NOT EXISTS 'pilot'@'{APP_VM}' IDENTIFIED BY 'secret';" in run_sql.call_args.args[0]
    )


def test_the_dedicated_server_listens_on_the_network_sized_for_the_whole_vm(manager) -> None:
    from pilot.core.mariadb_memory import calculate_mariadb_memory

    with patch.object(DedicatedMariaDBManager, "_total_memory_mb", return_value=16384):
        sizing = manager._write_config()

    content = manager.my_cnf_path.read_text()
    assert sizing == calculate_mariadb_memory(16384, memory_share=1.0)
    assert "bind-address = ::\nskip-name-resolve = ON\n" in content
    # The app VM's admin holds FILE, so SQL file access is kept to one empty directory.
    assert f"secure-file-priv = {manager.client_files_dir}\n" in content
    assert manager.client_files_dir.is_dir() and manager.client_files_dir.is_relative_to(manager.state_dir)
    assert "port = 3306\n" in content


def test_a_dedicated_config_gets_the_dedicated_manager_at_boot_too() -> None:
    from pilot.managers.database.mariadb import MariaDBManager

    # The boot sizing unit builds its manager this way, so it keeps the whole-VM sizing and the network listen.
    assert isinstance(MariaDBManager.for_config(MariaDBConfig(dedicated=True)), DedicatedMariaDBManager)
