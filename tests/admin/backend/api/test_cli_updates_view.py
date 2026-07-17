"""Tests for GET /api/v1/cli-updates and POST /api/v1/cli-update-checks."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from pilot.config.bench_toml_builder import BenchTomlBuilder


def _client(bench_root: Path, password: str = "secret"):
    from admin.backend.app import create_app
    from pilot.core.admin_auth import ensure_jwt_secret, issue_token

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


def _mock_repo(behind: int) -> Mock:
    repo = Mock()
    repo.branch = "main"
    repo.count.return_value = behind
    repo.commit_subject.return_value = "a commit"
    repo.last_fetched = None
    return repo


def test_cli_updates_reads_without_fetching(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    repo = _mock_repo(behind=2)

    with patch("admin.backend.api.v1.updates.cli_root", return_value=Path("/cli")), \
         patch("admin.backend.api.v1.updates.GitRepo", return_value=repo):
        response = client.get("/api/v1/cli-updates")

    body = response.get_json()
    assert response.status_code == 200
    assert body["commits_behind"] == 2
    assert body["update_available"] is True
    repo.fetch.assert_not_called()


def test_cli_update_checks_fetches_first(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    repo = _mock_repo(behind=0)

    with patch("admin.backend.api.v1.updates.cli_root", return_value=Path("/cli")), \
         patch("admin.backend.api.v1.updates.GitRepo", return_value=repo):
        response = client.post("/api/v1/cli-update-checks")

    body = response.get_json()
    assert response.status_code == 200
    assert body["update_available"] is False
    repo.fetch.assert_called_once_with("main", timeout=60)
