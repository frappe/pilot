from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.core.agent.client import API_VERSION
from pilot.managers.database.dedicated_mariadb import DedicatedMariaDBManager
from tests.pilot.core.agent.testing import APP_VM, TOKEN, make_client


@pytest.mark.parametrize(
    ("address", "token", "status"),
    [
        pytest.param(APP_VM, "wrong", 401, id="wrong-token"),
        pytest.param("fdaa:1:2::8", TOKEN, 403, id="other-address"),
        pytest.param("testclient", TOKEN, 403, id="not-an-address"),
        pytest.param("fdaa:1:2:0::7", TOKEN, 200, id="app-vm-written-differently"),
    ],
)
def test_only_the_app_vm_with_the_token_is_served(manager, address: str, token: str, status: int) -> None:
    # The node's and the roles' requests share one check.
    for path in ("/pilot/update", "/database/disk"):
        assert make_client(address, token).get(path).status_code == status


def test_health_reports_the_vm_the_callers_address_and_each_role(manager) -> None:
    with (
        patch.object(DedicatedMariaDBManager, "is_installed", return_value=True),
        patch.object(DedicatedMariaDBManager, "is_provisioned", return_value=True),
        patch.object(DedicatedMariaDBManager, "is_running", return_value=True),
        patch.object(DedicatedMariaDBManager, "get_server_version", return_value=(11, 8)),
        patch.object(DedicatedMariaDBManager, "_total_memory_mb", return_value=8192),
        patch("pilot.core.agent.app.get_installed_version", return_value="v0.1.0"),
    ):
        health = make_client().get("/health").json()

    assert health == {
        "api_version": API_VERSION,
        "pilot_version": "v0.1.0",
        "client_address": APP_VM,
        "roles": {
            "database": {
                "is_installed": True,
                "is_provisioned": True,
                "is_running": True,
                "version": [11, 8],
                "memory_mb": 8192,
            }
        },
    }


def test_a_vm_without_the_database_role_serves_no_database_requests() -> None:
    client = make_client(is_database_server=False)

    with patch("pilot.core.agent.app.get_installed_version", return_value="v0.1.0"):
        assert client.get("/health").json()["roles"] == {}
    assert client.get("/database/disk").status_code == 404


def test_metrics_is_one_system_log_record_with_the_pilot_root_disk(root: Path) -> None:
    from pilot.core.server.monitoring_proc import ProcMetricsReader

    disk = {"total_bytes": 100, "used_bytes": 40, "free_bytes": 60, "percent": 40.0}
    with (
        patch("pilot.core.server.monitoring_proc.time.sleep") as sleep,
        patch.object(ProcMetricsReader, "disk_usage", return_value=disk) as disk_usage,
    ):
        record = make_client(is_database_server=False).get("/metrics").json()

    sleep.assert_called_once()
    disk_usage.assert_called_once_with(root)
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
