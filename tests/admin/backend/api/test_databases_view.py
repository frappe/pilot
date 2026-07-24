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
    from admin.backend.auth import ensure_jwt_secret, issue_token

    bench_root.mkdir(parents=True, exist_ok=True)
    flat = {
        "admin_enabled": True,
        "admin_password": password,
        "admin_allow_bench_management": allow_bench_management,
        "db_type": db_type,
    }
    (bench_root / "bench.toml").write_text(BenchConfig.from_flat(bench_root.name, flat).dumps())
    secret = ensure_jwt_secret(bench_root / "bench.toml")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", issue_token(secret))
    return client


def test_diagnostics_returns_provider_payload(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    payload = {"active_connections": 2, "lock_waits": {}, "binlog": {}}
    with patch(f"{_PROVIDER}.get_diagnostics", return_value=payload), patch(f"{_PROVIDER}.__init__", return_value=None):
        response = client.get("/api/v1/database/diagnostics")

    assert response.status_code == 200
    assert response.get_json() == payload


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
    rows = [{"id": "42", "type": "RECORD", "mode": "X", "table": "tabDoc", "index": "PRIMARY",
             "state": "LOCK WAIT", "started": "2026-01-01T00:00:00", "query": "UPDATE tabDoc SET x=1",
             "rows_locked": 3, "rows_modified": 1}]
    patcher, _ = _patched_provider(**{"get_lock_wait_rows.return_value": rows})
    with patcher:
        response = client.get("/api/v1/database/lockwaits")

    assert response.status_code == 200
    assert response.get_json() == rows


def test_lockwaits_maps_unsupported_engine_to_422(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    patcher, _ = _patched_provider(
        **{"get_lock_wait_rows.side_effect": DatabaseError("The selected engine does not support this operation")}
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
