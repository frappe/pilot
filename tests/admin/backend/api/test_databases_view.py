"""Tests for the /api/v1/database diagnostics routes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from pilot.config import BenchConfig
from pilot.exceptions import DatabaseError

_PROVIDER = "admin.backend.providers.database.DatabaseDiagnosticsProvider"


def _patched_provider(**attributes):
    provider = Mock()
    provider.configure_mock(**attributes)
    return patch("admin.backend.api.v1.databases._provider", return_value=provider), provider


def _client(
    bench_root: Path,
    password: str = "secret",
    allow_bench_management: bool = True,
    db_type: str = "mariadb",
):
    from admin.backend.app import create_app
    from admin.backend.internal.session import Session
    from pilot.core.bench import Bench

    bench_root.mkdir(parents=True, exist_ok=True)
    flat = {
        "admin_enabled": True,
        "admin_password": password,
        "admin_allow_bench_management": allow_bench_management,
        "db_type": db_type,
    }
    (bench_root / "bench.toml").write_text(BenchConfig.from_flat(bench_root.name, flat).dumps())
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", Session(Bench(bench_root)).issue_session_token()[0])
    return client


def test_diagnostics_returns_provider_payload(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    payload = {"active_connections": 2, "lock_waits": {}, "binlog": {}}
    with (
        patch(f"{_PROVIDER}.get_diagnostics", return_value=payload),
        patch(f"{_PROVIDER}.__init__", return_value=None),
    ):
        response = client.get("/api/v1/database/diagnostics")

    assert response.status_code == 200
    assert response.get_json() == payload


def test_database_quick_actions_returns_capability_payload(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    payload = {
        "engine": "mariadb",
        "managed": True,
        "reachable": True,
        "actions": {
            "restart": {"available": True, "reason": "", "requires_restart": True},
            "performance_schema": {
                "available": True,
                "reason": "",
                "enabled": False,
                "requires_restart": True,
            },
            "innodb_buffer_pool_size": {
                "available": True,
                "reason": "",
                "current_mb": 128,
                "min_mb": 128,
                "max_mb": 352,
                "recommended_mb": 128,
                "dynamic_max_mb": 128,
                "unit": "MB",
                "requires_restart": False,
            },
            "max_connections": {
                "available": True,
                "reason": "",
                "current": 50,
                "min": 10,
                "max": 50,
                "recommended": 50,
                "requires_restart": False,
            },
            "manage_binlogs": {"available": True, "reason": ""},
        },
    }
    actions = Mock()
    actions.capabilities.return_value = payload
    with patch("admin.backend.api.v1.databases._quick_actions", return_value=actions):
        response = client.get("/api/v1/database/quick-actions")

    assert response.status_code == 200
    assert response.get_json() == payload


def test_database_configurations_returns_catalog_payload(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    payload = {
        "engine": "mariadb",
        "managed": True,
        "readable": True,
        "editable": True,
        "reason": "",
        "edit_reason": "",
        "variables": [
            {
                "name": "connect_timeout",
                "value": 10,
                "editable": True,
            }
        ],
    }
    configurations = Mock()
    configurations.snapshot.return_value = payload
    with patch(
        "admin.backend.api.v1.databases._configurations",
        return_value=configurations,
    ):
        response = client.get("/api/v1/database/configurations")

    assert response.status_code == 200
    assert response.get_json() == payload


def test_database_configuration_queues_typed_guarded_task(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    configurations = Mock()
    configurations.prepare_change.return_value = {
        "action": "configuration",
        "name": "connect_timeout",
        "value": 20,
        "current": 10,
        "changed": True,
    }
    with (
        patch(
            "admin.backend.api.v1.databases._configurations",
            return_value=configurations,
        ),
        patch(
            "admin.backend.api.v1.databases.SetMariaDBConfigurationTask.queue",
            return_value="task-config",
        ) as queue,
        patch(
            "admin.backend.api.v1.databases.accepted_task_response",
            return_value=({"task_id": "task-config"}, 202),
        ),
    ):
        response = client.post(
            "/api/v1/database/configurations/connect_timeout",
            json={"value": 20},
            headers={"Idempotency-Key": "connect-timeout-20"},
        )

    assert response.status_code == 202
    configurations.prepare_change.assert_called_once_with("connect_timeout", 20)
    assert queue.call_args.kwargs == {
        "variable": "connect_timeout",
        "value_json": "20",
        "idempotency_key": "connect-timeout-20",
        "resource_key": "database-server",
    }


@pytest.mark.parametrize(
    ("variable", "value", "current", "action", "task", "task_argument"),
    [
        (
            "max_connections",
            40,
            50,
            "max_connections",
            "SetMaxDatabaseConnectionsTask",
            {"max_connections": 40},
        ),
        (
            "innodb_buffer_pool_size",
            256,
            128,
            "innodb_buffer_pool_size",
            "SetInnoDBBufferPoolSizeTask",
            {"size_mb": 256},
        ),
        (
            "performance_schema",
            True,
            False,
            "performance_schema",
            "SetPerformanceSchemaTask",
            {"state": "enabled"},
        ),
    ],
)
def test_database_configuration_routes_guarded_variables_to_specialized_tasks(
    tmp_path: Path,
    variable: str,
    value,
    current,
    action: str,
    task: str,
    task_argument: dict,
) -> None:
    client = _client(tmp_path / "benches" / "current")
    configurations = Mock()
    configurations.prepare_change.return_value = {
        "action": action,
        "name": variable,
        "value": value,
        "current": current,
        "changed": True,
    }
    with (
        patch(
            "admin.backend.api.v1.databases._configurations",
            return_value=configurations,
        ),
        patch(
            f"admin.backend.api.v1.databases.{task}.queue",
            return_value="task-database-configuration",
        ) as queue,
        patch(
            "admin.backend.api.v1.databases.accepted_task_response",
            return_value=({"task_id": "task-database-configuration"}, 202),
        ),
    ):
        response = client.post(
            f"/api/v1/database/configurations/{variable}",
            json={"value": value},
            headers={"Idempotency-Key": "config-guarded-variable"},
        )

    assert response.status_code == 202
    configurations.prepare_change.assert_called_once_with(variable, value)
    assert queue.call_args.kwargs == {
        **task_argument,
        "idempotency_key": "config-guarded-variable",
        "resource_key": "database-server",
    }


@pytest.mark.parametrize(
    "payload",
    [None, [], {}, {"other": 20}, {"value": 20, "other": True}],
)
def test_database_configuration_requires_exact_value_payload(
    tmp_path: Path,
    payload,
) -> None:
    client = _client(tmp_path / "benches" / "current")
    response = client.post(
        "/api/v1/database/configurations/connect_timeout",
        json=payload,
    )

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_database_configuration"


def test_unchanged_database_configuration_does_not_queue_task(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    configurations = Mock()
    configurations.prepare_change.return_value = {
        "name": "connect_timeout",
        "value": 10,
        "current": 10,
        "changed": False,
    }
    with (
        patch(
            "admin.backend.api.v1.databases._configurations",
            return_value=configurations,
        ),
        patch("admin.backend.api.v1.databases.SetMariaDBConfigurationTask.queue") as queue,
    ):
        response = client.post(
            "/api/v1/database/configurations/connect_timeout",
            json={"value": 10},
        )

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "database_configuration_unchanged"
    queue.assert_not_called()


def test_database_configuration_validation_error_is_unprocessable(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    configurations = Mock()
    configurations.prepare_change.side_effect = ValueError(
        "Connection handshake timeout must be at least 2 seconds."
    )
    with patch(
        "admin.backend.api.v1.databases._configurations",
        return_value=configurations,
    ):
        response = client.post(
            "/api/v1/database/configurations/connect_timeout",
            json={"value": 1},
        )

    assert response.status_code == 422
    assert response.get_json()["error"]["message"] == (
        "Connection handshake timeout must be at least 2 seconds."
    )


def test_database_configuration_policy_error_is_conflict(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    configurations = Mock()
    configurations.prepare_change.side_effect = DatabaseError(
        "External MariaDB configurations are read-only."
    )
    with patch(
        "admin.backend.api.v1.databases._configurations",
        return_value=configurations,
    ):
        response = client.post(
            "/api/v1/database/configurations/connect_timeout",
            json={"value": 20},
        )

    assert response.status_code == 409
    assert response.get_json()["error"]["message"] == ("External MariaDB configurations are read-only.")


def test_restart_database_queues_guarded_non_cancellable_task(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    actions = Mock()
    with (
        patch("admin.backend.api.v1.databases._quick_actions", return_value=actions),
        patch("admin.backend.api.v1.databases.RestartDatabaseTask.queue", return_value="task-1") as queue,
        patch(
            "admin.backend.api.v1.databases.accepted_task_response",
            return_value=({"task_id": "task-1"}, 202),
        ),
    ):
        response = client.post(
            "/api/v1/database/quick-actions/restart",
            headers={"Idempotency-Key": "restart-once"},
        )

    assert response.status_code == 202
    assert response.get_json() == {"task_id": "task-1"}
    actions.require_restart.assert_called_once()
    assert queue.call_args.kwargs == {
        "idempotency_key": "restart-once",
        "resource_key": "database-server",
    }


def test_restart_database_returns_capability_reason(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    actions = Mock()
    actions.require_restart.side_effect = DatabaseError("Pilot cannot restart an external MariaDB server.")
    with (
        patch("admin.backend.api.v1.databases._quick_actions", return_value=actions),
        patch("admin.backend.api.v1.databases.RestartDatabaseTask.queue") as queue,
    ):
        response = client.post("/api/v1/database/quick-actions/restart")

    assert response.status_code == 409
    assert response.get_json()["error"]["message"] == "Pilot cannot restart an external MariaDB server."
    queue.assert_not_called()


@pytest.mark.parametrize("payload", [{}, {"enabled": 1}, {"enabled": "true"}, {"enabled": None}, []])
def test_performance_schema_rejects_non_boolean(
    tmp_path: Path,
    payload,
) -> None:
    client = _client(tmp_path / "benches" / "current")
    response = client.post("/api/v1/database/quick-actions/performance-schema", json=payload)

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_enabled"


def test_performance_schema_queues_guarded_task(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    actions = Mock()
    actions.require_performance_schema.return_value = {"enabled": False}
    with (
        patch("admin.backend.api.v1.databases._quick_actions", return_value=actions),
        patch(
            "admin.backend.api.v1.databases.SetPerformanceSchemaTask.queue",
            return_value="task-2",
        ) as queue,
        patch(
            "admin.backend.api.v1.databases.accepted_task_response",
            return_value=({"task_id": "task-2"}, 202),
        ),
    ):
        response = client.post(
            "/api/v1/database/quick-actions/performance-schema",
            json={"enabled": True},
            headers={"Idempotency-Key": "performance-on"},
        )

    assert response.status_code == 202
    actions.require_performance_schema.assert_called_once()
    assert queue.call_args.kwargs == {
        "state": "enabled",
        "idempotency_key": "performance-on",
        "resource_key": "database-server",
    }


@pytest.mark.parametrize(
    ("path", "payload", "error_code"),
    [
        ("/api/v1/database/quick-actions/innodb-buffer-pool-size", {}, "invalid_size_mb"),
        (
            "/api/v1/database/quick-actions/innodb-buffer-pool-size",
            {"size_mb": True},
            "invalid_size_mb",
        ),
        (
            "/api/v1/database/quick-actions/innodb-buffer-pool-size",
            {"size_mb": "256"},
            "invalid_size_mb",
        ),
        ("/api/v1/database/quick-actions/max-connections", {}, "invalid_max_connections"),
        (
            "/api/v1/database/quick-actions/max-connections",
            {"max_connections": 40.5},
            "invalid_max_connections",
        ),
        (
            "/api/v1/database/quick-actions/max-connections",
            {"max_connections": "40"},
            "invalid_max_connections",
        ),
    ],
)
def test_sizing_actions_reject_non_integer_payloads(
    tmp_path: Path,
    path: str,
    payload,
    error_code: str,
) -> None:
    client = _client(tmp_path / "benches" / "current")
    response = client.post(path, json=payload)

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == error_code


def test_innodb_buffer_pool_size_queues_guarded_task(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    actions = Mock()
    actions.require_innodb_buffer_pool_size.return_value = {"current_mb": 128}
    with (
        patch("admin.backend.api.v1.databases._quick_actions", return_value=actions),
        patch(
            "admin.backend.api.v1.databases.SetInnoDBBufferPoolSizeTask.queue",
            return_value="task-3",
        ) as queue,
        patch(
            "admin.backend.api.v1.databases.accepted_task_response",
            return_value=({"task_id": "task-3"}, 202),
        ),
    ):
        response = client.post(
            "/api/v1/database/quick-actions/innodb-buffer-pool-size",
            json={"size_mb": 256},
            headers={"Idempotency-Key": "pool-256"},
        )

    assert response.status_code == 202
    actions.require_innodb_buffer_pool_size.assert_called_once_with(256)
    assert queue.call_args.kwargs == {
        "size_mb": 256,
        "idempotency_key": "pool-256",
        "resource_key": "database-server",
    }


def test_max_connections_queues_guarded_task(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    actions = Mock()
    actions.require_max_connections.return_value = {"current": 50}
    with (
        patch("admin.backend.api.v1.databases._quick_actions", return_value=actions),
        patch(
            "admin.backend.api.v1.databases.SetMaxDatabaseConnectionsTask.queue",
            return_value="task-4",
        ) as queue,
        patch(
            "admin.backend.api.v1.databases.accepted_task_response",
            return_value=({"task_id": "task-4"}, 202),
        ),
    ):
        response = client.post(
            "/api/v1/database/quick-actions/max-connections",
            json={"max_connections": 40},
            headers={"Idempotency-Key": "connections-40"},
        )

    assert response.status_code == 202
    actions.require_max_connections.assert_called_once_with(40)
    assert queue.call_args.kwargs == {
        "max_connections": 40,
        "idempotency_key": "connections-40",
        "resource_key": "database-server",
    }


@pytest.mark.parametrize(
    ("path", "payload", "require_method", "capability", "queue_target"),
    [
        (
            "/api/v1/database/quick-actions/performance-schema",
            {"enabled": True},
            "require_performance_schema",
            {"enabled": True},
            "admin.backend.api.v1.databases.SetPerformanceSchemaTask.queue",
        ),
        (
            "/api/v1/database/quick-actions/innodb-buffer-pool-size",
            {"size_mb": 128},
            "require_innodb_buffer_pool_size",
            {"current_mb": 128},
            "admin.backend.api.v1.databases.SetInnoDBBufferPoolSizeTask.queue",
        ),
        (
            "/api/v1/database/quick-actions/max-connections",
            {"max_connections": 50},
            "require_max_connections",
            {"current": 50},
            "admin.backend.api.v1.databases.SetMaxDatabaseConnectionsTask.queue",
        ),
    ],
)
def test_unchanged_database_actions_do_not_queue_tasks(
    tmp_path: Path,
    path: str,
    payload: dict,
    require_method: str,
    capability: dict,
    queue_target: str,
) -> None:
    client = _client(tmp_path / "benches" / "current")
    actions = Mock()
    getattr(actions, require_method).return_value = capability

    with (
        patch("admin.backend.api.v1.databases._quick_actions", return_value=actions),
        patch(queue_target) as queue,
    ):
        response = client.post(path, json=payload)

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "database_action_unchanged"
    queue.assert_not_called()


@pytest.mark.parametrize(
    ("path", "payload", "method", "message", "error_code"),
    [
        (
            "/api/v1/database/quick-actions/innodb-buffer-pool-size",
            {"size_mb": 500},
            "require_innodb_buffer_pool_size",
            "size_mb must be between 128 and 352 MB.",
            "invalid_size_mb",
        ),
        (
            "/api/v1/database/quick-actions/max-connections",
            {"max_connections": 100},
            "require_max_connections",
            "max_connections must be between 10 and 50.",
            "invalid_max_connections",
        ),
    ],
)
def test_sizing_actions_return_range_errors_as_unprocessable(
    tmp_path: Path,
    path: str,
    payload,
    method: str,
    message: str,
    error_code: str,
) -> None:
    client = _client(tmp_path / "benches" / "current")
    actions = Mock()
    getattr(actions, method).side_effect = ValueError(message)
    with patch("admin.backend.api.v1.databases._quick_actions", return_value=actions):
        response = client.post(path, json=payload)

    assert response.status_code == 422
    assert response.get_json()["error"] == {
        "code": error_code,
        "message": message,
        "details": {},
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/database/configurations/connect_timeout",
        "/api/v1/database/quick-actions/restart",
        "/api/v1/database/quick-actions/performance-schema",
        "/api/v1/database/quick-actions/innodb-buffer-pool-size",
        "/api/v1/database/quick-actions/max-connections",
    ],
)
def test_database_mutations_are_forbidden_when_bench_management_is_disabled(
    tmp_path: Path,
    path: str,
) -> None:
    client = _client(
        tmp_path / "benches" / "current",
        allow_bench_management=False,
    )
    with patch("admin.backend.api.v1.databases._quick_actions") as actions:
        response = client.post(path)

    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "bench_management_forbidden"
    actions.assert_not_called()


def test_diagnostics_maps_unexpected_failure_to_500(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    patcher, _ = _patched_provider(**{"get_diagnostics.side_effect": RuntimeError("boom")})
    with patcher:
        response = client.get("/api/v1/database/diagnostics")

    assert response.status_code == 500
    assert response.get_json()["error"]["code"] == "diagnostics_unavailable"


def test_diagnostics_surfaces_database_error_message(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    patcher, _ = _patched_provider(**{"get_diagnostics.side_effect": DatabaseError("server is gone")})
    with patcher:
        response = client.get("/api/v1/database/diagnostics")

    assert response.status_code == 422
    assert response.get_json()["error"]["message"] == "server is gone"


def test_binlogs_lists_files(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    files = [{"name": "mysql-bin.000001", "size_bytes": 1024, "modified_ms": None}]
    patcher, _ = _patched_provider(**{"get_binlog_files.return_value": files})
    with patcher:
        response = client.get("/api/v1/database/binlogs")

    assert response.status_code == 200
    assert response.get_json() == files


def test_lockwaits_lists_rows(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    rows = [
        {
            "id": "42",
            "type": "RECORD",
            "mode": "X",
            "table": "tabDoc",
            "index": "PRIMARY",
            "state": "LOCK WAIT",
            "started": "2026-01-01T00:00:00",
            "query": "UPDATE tabDoc SET x=1",
            "rows_locked": 3,
            "rows_modified": 1,
        }
    ]
    patcher, _ = _patched_provider(**{"get_lock_wait_rows.return_value": rows})
    with patcher:
        response = client.get("/api/v1/database/lockwaits")

    assert response.status_code == 200
    assert response.get_json() == rows


def test_lockwaits_maps_unsupported_engine_to_422(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    patcher, _ = _patched_provider(
        **{
            "get_lock_wait_rows.side_effect": DatabaseError(
                "The selected engine does not support this operation"
            )
        }
    )
    with patcher:
        response = client.get("/api/v1/database/lockwaits")

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "lockwaits_unavailable"


def test_kill_process_succeeds(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    patcher, provider = _patched_provider()
    with patcher:
        response = client.post("/api/v1/database/processlist/kill", json={"process_id": 4096})

    assert response.status_code == 200
    provider.kill_process.assert_called_once_with(4096)


@pytest.mark.parametrize("process_id", ["4096", 0, -1, True, None, 7.5])
def test_kill_process_rejects_bad_ids(tmp_path: Path, process_id) -> None:
    client = _client(tmp_path / "benches" / "current")
    patcher, provider = _patched_provider()
    with patcher:
        response = client.post("/api/v1/database/processlist/kill", json={"process_id": process_id})

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_process_id"
    provider.kill_process.assert_not_called()


def test_kill_process_maps_missing_process_to_422(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    patcher, _ = _patched_provider(**{"kill_process.side_effect": DatabaseError("Unknown thread id: 9")})
    with patcher:
        response = client.post("/api/v1/database/processlist/kill", json={"process_id": 9})

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "kill_failed"


def test_kill_process_forbidden_when_bench_management_disabled(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current", allow_bench_management=False)
    patcher, provider = _patched_provider()
    with patcher:
        response = client.post("/api/v1/database/processlist/kill", json={"process_id": 4096})

    assert response.status_code == 403
    provider.kill_process.assert_not_called()


def test_purge_requires_up_to(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    response = client.post("/api/v1/database/binlogs/purge", json={})
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_up_to"


def test_purge_maps_unknown_file_to_422(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    patcher, _ = _patched_provider(**{"purge_binlogs.side_effect": DatabaseError("Unknown binlog file: x")})
    with patcher:
        response = client.post("/api/v1/database/binlogs/purge", json={"up_to": "x"})

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "purge_failed"


def test_purge_forbidden_when_bench_management_disabled(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current", allow_bench_management=False)
    with patch(f"{_PROVIDER}.purge_binlogs") as purge, patch(f"{_PROVIDER}.__init__", return_value=None):
        response = client.post("/api/v1/database/binlogs/purge", json={"up_to": "mysql-bin.000002"})

    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "bench_management_forbidden"
    purge.assert_not_called()


def test_binlog_listing_still_allowed_when_bench_management_disabled(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current", allow_bench_management=False)
    patcher, _ = _patched_provider(**{"get_binlog_files.return_value": []})
    with patcher:
        response = client.get("/api/v1/database/binlogs")

    assert response.status_code == 200


def test_diagnostics_reports_unsupported_for_sqlite_bench(tmp_path: Path) -> None:
    from admin.backend.providers.database import NO_DATABASE_SERVER

    client = _client(tmp_path / "benches" / "current", db_type="sqlite")
    response = client.get("/api/v1/database/diagnostics")

    assert response.status_code == 200
    assert response.get_json() == {
        "engine": "sqlite",
        "supported": False,
        "reason": NO_DATABASE_SERVER,
    }


def test_binlogs_rejected_for_sqlite_bench(tmp_path: Path) -> None:
    from admin.backend.providers.database import NO_DATABASE_SERVER

    client = _client(tmp_path / "benches" / "current", db_type="sqlite")
    response = client.get("/api/v1/database/binlogs")

    assert response.status_code == 422
    assert response.get_json()["error"]["message"] == NO_DATABASE_SERVER


def test_purge_rejected_for_sqlite_bench(tmp_path: Path) -> None:
    from admin.backend.providers.database import NO_DATABASE_SERVER

    client = _client(tmp_path / "benches" / "current", db_type="sqlite")
    response = client.post("/api/v1/database/binlogs/purge", json={"up_to": "mysql-bin.000002"})

    assert response.status_code == 422
    assert response.get_json()["error"]["message"] == NO_DATABASE_SERVER


def test_purge_succeeds(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    patcher, provider = _patched_provider()
    with patcher:
        response = client.post("/api/v1/database/binlogs/purge", json={"up_to": " mysql-bin.000002 "})

    assert response.status_code == 200
    provider.purge_binlogs.assert_called_once_with("mysql-bin.000002")


def test_a_performance_section_returns_the_provider_payload(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    section = {
        "data": [
            {
                "database": "site_db",
                "table": "tabUser",
                "redundant_index": "lft",
                "redundant_index_columns": "lft",
                "dominant_index": "lft_rgt",
                "dominant_index_columns": "lft,rgt",
            }
        ],
        "has_next_page": True,
    }
    patcher, provider = _patched_provider(**{"get_redundant_indexes.return_value": section})
    with patcher:
        response = client.get("/api/v1/database/performance-report?report_type=redundant_indexes&site=shop.local")

    assert response.status_code == 200
    assert response.get_json() == section
    provider.get_redundant_indexes.assert_called_once_with("shop.local", 20, 0)


def test_a_performance_section_defaults_to_the_whole_server_and_first_page(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    patcher, provider = _patched_provider(**{"get_unused_indexes.return_value": {}})
    with patcher:
        client.get("/api/v1/database/performance-report?report_type=unused_indexes")

    provider.get_unused_indexes.assert_called_once_with("", 20, 0)


def test_a_performance_section_passes_the_requested_page_through(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    patcher, provider = _patched_provider(**{"get_unused_indexes.return_value": {}})
    with patcher:
        client.get("/api/v1/database/performance-report?report_type=unused_indexes&limit=50&offset=100")

    provider.get_unused_indexes.assert_called_once_with("", 50, 100)


def test_a_performance_section_rejects_a_non_numeric_page(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    patcher, _ = _patched_provider(**{"get_unused_indexes.return_value": {}})
    with patcher:
        response = client.get("/api/v1/database/performance-report?report_type=unused_indexes&limit=all")

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_page"


def test_an_unknown_report_type_is_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    patcher, _ = _patched_provider(**{"get_unused_indexes.return_value": {}})
    with patcher:
        response = client.get("/api/v1/database/performance-report?report_type=nope")

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_report_type"


def test_a_performance_section_maps_unsupported_engine_to_422(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    patcher, _ = _patched_provider(
        **{
            "get_time_consuming_queries.side_effect": DatabaseError(
                "The selected engine does not support this operation"
            )
        }
    )
    with patcher:
        response = client.get("/api/v1/database/performance-report?report_type=time_consuming_queries")

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "performance_report_unavailable"
