"""Tests for /api/v1/metrics."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pilot.config import BenchConfig
from pilot.exceptions import CommandError


def _client(bench_root: Path, password: str = "secret"):
    from admin.backend.app import create_app
    from admin.backend.internal.session import Session
    from pilot.core.bench import Bench

    bench_root.mkdir(parents=True, exist_ok=True)
    (bench_root / "bench.toml").write_text(
        BenchConfig.from_flat(bench_root.name, {"admin_enabled": True, "admin_password": password}).dumps()
    )
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", Session(Bench(bench_root)).issue_session_token()[0])
    return client


def test_metrics_does_not_measure_directories(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "current")

    with patch(
        "admin.backend.providers.storage.directory_bytes",
        side_effect=CommandError("du failed"),
    ) as directory_bytes:
        response = client.get("/api/v1/metrics")

    assert response.status_code == 200
    assert "paths" not in response.get_json()
    directory_bytes.assert_not_called()
