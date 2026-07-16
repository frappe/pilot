"""Tests for GET /api/v1/cli-updates and POST /api/v1/cli-update-checks."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pilot.config.bench_toml_builder import BenchTomlBuilder


def _client(bench_root: Path, password: str = "secret"):
    from admin.backend.app import create_app
    from pilot.commands.admin.generate_session import ensure_jwt_secret, issue_token

    bench_root.mkdir(parents=True, exist_ok=True)
    (bench_root / "bench.toml").write_text(
        BenchTomlBuilder(bench_root.name, {"admin_enabled": True, "admin_password": password}).render()
    )
    secret = ensure_jwt_secret(bench_root / "bench.toml")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", issue_token(secret))
    return client


def test_cli_updates_reads_without_fetching(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    with patch("admin.backend.api.v1.updates.cli_root", return_value=Path("/cli")), \
         patch("admin.backend.api.v1.updates._current_branch", return_value="main"), \
         patch("admin.backend.api.v1.updates._git_fetch") as git_fetch, \
         patch("admin.backend.api.v1.updates._count", return_value=2), \
         patch("admin.backend.api.v1.updates._log_subject", return_value="a commit"):
        response = client.get("/api/v1/cli-updates")

    body = response.get_json()
    assert response.status_code == 200
    assert body["commits_behind"] == 2
    assert body["update_available"] is True
    git_fetch.assert_not_called()


def test_cli_update_checks_fetches_first(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    with patch("admin.backend.api.v1.updates.cli_root", return_value=Path("/cli")), \
         patch("admin.backend.api.v1.updates._current_branch", return_value="main"), \
         patch("admin.backend.api.v1.updates._git_fetch") as git_fetch, \
         patch("admin.backend.api.v1.updates._count", return_value=0), \
         patch("admin.backend.api.v1.updates._log_subject", return_value="a commit"):
        response = client.post("/api/v1/cli-update-checks")

    body = response.get_json()
    assert response.status_code == 200
    assert body["update_available"] is False
    git_fetch.assert_called_once_with(Path("/cli"), "main")
