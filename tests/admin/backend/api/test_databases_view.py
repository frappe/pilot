"""Tests for the /api/v1/database diagnostics routes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pilot.config import BenchConfig
from pilot.exceptions import DatabaseError

_PROVIDER = "admin.backend.providers.database.DatabaseDiagnosticsProvider"


def _client(bench_root: Path, password: str = "secret"):
    from admin.backend.app import create_app
    from admin.backend.auth import ensure_jwt_secret, issue_token

    bench_root.mkdir(parents=True, exist_ok=True)
    (bench_root / "bench.toml").write_text(
        BenchConfig.from_flat(bench_root.name, {"admin_enabled": True, "admin_password": password}).dumps()
    )
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


def test_diagnostics_maps_failure_to_500(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    with patch(f"{_PROVIDER}.get_diagnostics", side_effect=DatabaseError("down")), patch(
        f"{_PROVIDER}.__init__", return_value=None
    ):
        response = client.get("/api/v1/database/diagnostics")

    assert response.status_code == 500
    assert response.get_json()["error"]["code"] == "diagnostics_unavailable"


def test_binlogs_lists_files(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    files = [{"name": "mysql-bin.000001", "size_bytes": 1024, "modified_ms": None}]
    with patch(f"{_PROVIDER}.get_binlog_files", return_value=files), patch(f"{_PROVIDER}.__init__", return_value=None):
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
    with patch(f"{_PROVIDER}.purge_binlogs", side_effect=DatabaseError("Unknown binlog file: x")), patch(
        f"{_PROVIDER}.__init__", return_value=None
    ):
        response = client.post("/api/v1/database/binlogs/purge", json={"up_to": "x"})

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "purge_failed"


def test_purge_succeeds(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")
    with patch(f"{_PROVIDER}.purge_binlogs", return_value=None) as purge, patch(
        f"{_PROVIDER}.__init__", return_value=None
    ):
        response = client.post("/api/v1/database/binlogs/purge", json={"up_to": " mysql-bin.000002 "})

    assert response.status_code == 200
    purge.assert_called_once_with("mysql-bin.000002")
