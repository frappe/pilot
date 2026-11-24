from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from admin.backend.providers.server.monitoring import SystemMonitoringProvider
from pilot.config import BenchConfig, MariaDBConfig, RedisConfig, WorkerConfig
from pilot.config.monitor import database_server_log_path, system_log_path


def _record(cpu_user: float, memory_total: int) -> dict:
    return {
        "time": datetime.now(UTC).isoformat(),
        "load_avg": [0.5, 0.4, 0.3],
        "cpu_percent": cpu_user,
        "cpu_breakdown": {
            "user": cpu_user,
            "system": 0.0,
            "iowait": 0.0,
            "irq": 0.0,
            "other": 0.0,
            "idle": 0.0,
        },
        "memory": {"total_bytes": memory_total, "used_bytes": 1, "cached_bytes": 1, "free_bytes": 1},
        "storage": {"disk": {"total_bytes": 100, "used_bytes": 40, "free_bytes": 60, "percent": 40.0}},
        "network": {"rx_bytes_per_sec": 1.0, "tx_bytes_per_sec": 2.0},
        "disk_io": {"read_bytes_per_sec": 3.0, "write_bytes_per_sec": 4.0},
    }


def _history(root: Path, mariadb: MariaDBConfig) -> dict:
    config = BenchConfig(
        name="main", python_version="3.14", mariadb=mariadb, redis=RedisConfig(), workers=WorkerConfig()
    )
    with patch.object(BenchConfig, "read", return_value=config):
        return SystemMonitoringProvider(root, "1h").get_history()


def test_history_has_the_database_server_series_only_for_a_remote_server(tmp_path: Path) -> None:
    with patch("pilot.core.server.Server.root", tmp_path):
        system_log_path().parent.mkdir(parents=True)
        system_log_path().write_text(json.dumps(_record(cpu_user=10.0, memory_total=8)) + "\n")
        database_server_log_path().write_text(json.dumps(_record(cpu_user=70.0, memory_total=16)) + "\n")
        local = _history(tmp_path, MariaDBConfig())
        remote = _history(tmp_path, MariaDBConfig(remote=True, host="fdaa:1:2::9", agent_token="token"))

    assert local["database_server"] is None
    assert local["system"] == remote["system"]
    assert remote["system"]["points"][0]["Busy User"] == 10.0
    database_server = remote["database_server"]
    assert set(database_server) == set(remote["system"])
    assert database_server["points"][0]["Busy User"] == 70.0
    assert database_server["memory_total_bytes"] == 16
    assert database_server["storage"] == {"disk": {"used_bytes": 40, "total_bytes": 100, "percent": 40.0}}
