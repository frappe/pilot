"""Tests for the /api/v1/database diagnostics routes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

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
    assert response.get_json() == {"supported": False, "reason": NO_DATABASE_SERVER}


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
