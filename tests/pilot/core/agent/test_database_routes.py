from __future__ import annotations

from unittest.mock import patch

from pilot.managers.database.dedicated_mariadb import DedicatedMariaDBManager
from tests.pilot.core.agent.testing import APP_VM, make_client


def test_the_managed_config_takes_only_options_pilot_manages(manager) -> None:
    client = make_client()

    accepted = client.put("/database/config/managed", json={"content": "[mysqld]\nmax-connections = 300\n"})
    refused = client.put("/database/config/managed", json={"content": "[mysqld]\ninit-file = /tmp/x.sql\n"})

    assert accepted.status_code == 200
    assert refused.status_code == 422
    assert "init-file" in refused.json()["detail"]
    assert client.get("/database/config/managed").json() == {"content": "[mysqld]\nmax-connections = 300\n"}


def test_a_service_action_outside_the_list_is_refused(manager) -> None:
    with patch.object(DedicatedMariaDBManager, "control") as control:
        assert make_client().post("/database/service/restart").status_code == 200
        assert make_client().post("/database/service/disable").status_code == 422
    control.assert_called_once_with("restart")


def test_server_files_are_read_and_other_files_refused(manager) -> None:
    manager.error_log_path.write_text("first\nlast\n")
    client = make_client()

    log = client.get("/database/files", params={"path": str(manager.error_log_path), "tail_bytes": 5}).json()
    missing = client.get("/database/files", params={"path": str(manager.data_dir / "mysql-bin.index")}).json()
    outside = manager.data_dir / ".." / ".." / "config" / "my.cnf"
    refused = client.get("/database/files", params={"path": str(outside)})

    assert log == {"exists": True, "size_bytes": 11, "text": "last\n"}
    assert missing == {"exists": False, "size_bytes": 0, "text": ""}
    assert refused.status_code == 422


def test_the_admin_account_is_granted_to_the_callers_address(manager) -> None:
    with patch.object(DedicatedMariaDBManager, "_run_sql_as_superuser") as run_sql:
        response = make_client().post(
            "/database/accounts/admin", json={"user": "pilot", "password": "secret"}
        )

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
