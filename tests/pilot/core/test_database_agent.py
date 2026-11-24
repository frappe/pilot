from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest
from fastapi.testclient import TestClient

from pilot.config import MariaDBConfig
from pilot.config.common import CommonConfig
from pilot.config.database_agent import DatabaseAgentConfig
from pilot.core.database.agent.app import create_app
from pilot.core.database.agent.client import API_VERSION, DatabaseAgentClient, DatabaseAgentRefusedError
from pilot.core.database.agent.update import DatabaseServerUpdate
from pilot.exceptions import CommandError, DatabaseError
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
        patch("pilot.core.database.agent.app.get_installed_version", return_value="v0.1.0"),
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
        "pilot_version": "v0.1.0",
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


def test_metrics_is_one_system_log_record_with_the_data_directory_disk(manager) -> None:
    from pilot.core.server.monitoring_proc import ProcMetricsReader

    disk = {"total_bytes": 100, "used_bytes": 40, "free_bytes": 60, "percent": 40.0}
    with (
        patch("pilot.core.server.monitoring_proc.time.sleep") as sleep,
        patch.object(ProcMetricsReader, "disk_usage", return_value=disk) as disk_usage,
    ):
        record = _client().get("/metrics").json()

    sleep.assert_called_once()
    disk_usage.assert_called_once_with(manager.data_dir)
    assert set(record) == {
        "time",
        "load_avg",
        "cpu_percent",
        "cpu_breakdown",
        "memory",
        "storage",
        "network",
        "disk_io",
    }
    assert record["storage"] == {"disk": disk}
    assert len(record["load_avg"]) == 3
    assert set(record["cpu_breakdown"]) == {"user", "system", "iowait", "irq", "other", "idle"}
    assert set(record["network"]) == {"rx_bytes_per_sec", "tx_bytes_per_sec"}
    assert set(record["disk_io"]) == {"read_bytes_per_sec", "write_bytes_per_sec"}
    assert record["memory"]["total_bytes"] > 0


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


@pytest.fixture
def pilot_update(root: Path):
    """The update's launch and unit state, recorded instead of run."""
    with (
        patch("pilot.core.database.agent.update.run_command") as launch,
        patch.object(
            DatabaseServerUpdate, "is_unit_active", new_callable=PropertyMock, return_value=True
        ) as active,
        patch("pilot.core.database.agent.update.get_installed_version", return_value="dev 1111111"),
    ):
        yield launch, active


def test_a_pilot_update_runs_outside_the_agent_and_a_second_start_joins_it(manager, pilot_update) -> None:
    launch, active = pilot_update
    client = _client()

    first = client.post("/pilot/update").json()
    second = client.post("/pilot/update").json()

    assert first == second == client.get("/pilot/update").json()
    assert (first["status"], first["version_before"]) == ("running", "dev 1111111")
    launch.assert_called_once()
    command = launch.call_args.args[0]
    assert command[:3] == ["systemd-run", "--user", "--unit=pilot-database-update.service"]
    assert command[-3:] == ["-P", "-m", "pilot.core.database.agent.update"]

    # A run whose unit is gone, for example after a reboot, has failed and does not block a new one.
    active.return_value = False
    assert "stopped before it finished" in client.get("/pilot/update").json()["error"]
    launch.side_effect = CommandError("systemd-run failed")
    refused = client.post("/pilot/update")
    assert refused.status_code == 422
    assert client.get("/pilot/update").json()["error"] == "systemd-run failed"


def test_the_update_records_its_result_for_the_app_vm(manager, pilot_update) -> None:
    update = DatabaseServerUpdate()
    update.start()
    with patch("pilot.core.database.agent.update.perform_upgrade"):
        update.run()
    assert update.get_state()["status"] == "succeeded"

    update.start()
    with (
        patch(
            "pilot.core.database.agent.update.perform_upgrade", side_effect=CommandError("git pull failed.")
        ),
        pytest.raises(CommandError),
    ):
        update.run()
    state = _client().get("/pilot/update").json()
    assert (state["status"], state["version_after"]) == ("failed", "dev 1111111")
    assert state["error"] == f"git pull failed. See {update.log_path} on the database VM."


def _answers(*answers):
    """An agent client whose requests return or raise these answers in order."""
    agent = DatabaseAgentClient("fdaa:1:2::9", 7781, TOKEN)
    agent.pilot_update_poll_interval = 0
    return agent, patch.object(DatabaseAgentClient, "request", side_effect=answers)


def test_the_app_vm_waits_for_the_update_through_the_agent_restart() -> None:
    running = {"status": "running", "error": ""}
    done = {"status": "succeeded", "error": "", "version_before": "dev 1", "version_after": "dev 2"}
    agent, requests = _answers(running, DatabaseError("Pilot cannot reach the database agent"), done)
    progress = []

    with requests as request:
        assert agent.update_pilot(on_progress=progress.append) == done

    assert [call.args for call in request.call_args_list] == [
        ("POST", "/pilot/update"),
        ("GET", "/pilot/update"),
        ("GET", "/pilot/update"),
    ]
    assert progress[-1] == "Pilot on fdaa:1:2::9 updated: dev 1 -> dev 2."


@pytest.mark.parametrize(
    ("answers", "timeout", "message"),
    [
        (
            ({"status": "running"}, {"status": "failed", "error": "git pull failed."}),
            60,
            "update: git pull failed.",
        ),
        ((DatabaseAgentRefusedError("refused: Not Found", 404),), 60, "too old to update itself"),
        (({"status": "running"},), 0, "did not finish updating in 0 minutes"),
    ],
)
def test_the_app_vm_fails_with_the_reason_when_the_update_does_not_succeed(answers, timeout, message) -> None:
    agent, requests = _answers(*answers)
    agent.pilot_update_timeout = timeout

    with requests, pytest.raises(DatabaseError, match=message):
        agent.update_pilot()
