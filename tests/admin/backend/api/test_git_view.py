"""Tests for /api/v1/git/connection, /repositories, /branches, /repository-resolutions."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from pilot.config.bench_toml_builder import BenchTomlBuilder
from pilot.integrations.git import GitAuthError


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


def test_get_connection_reports_disconnected_by_default(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = client.get("/api/v1/git/connection")

    body = response.get_json()
    assert response.status_code == 200
    assert body["connected"] is False


def test_put_connection_saves_a_valid_token(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    provider = Mock()
    provider.validate.return_value = {"login": "octocat"}

    with patch("admin.backend.api.v1.git.provider_for_name", return_value=provider):
        response = client.put(
            "/api/v1/git/connection",
            json={"provider": "github", "token": "ghp_abc123"},
        )

    body = response.get_json()
    assert response.status_code == 200
    assert body["connected"] is True
    assert body["username"] == "octocat"
    assert "account" not in body
    assert "ok" not in body


def test_put_connection_rejects_an_invalid_token(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    provider = Mock()
    provider.validate.side_effect = GitAuthError()

    with patch("admin.backend.api.v1.git.provider_for_name", return_value=provider):
        response = client.put(
            "/api/v1/git/connection",
            json={"provider": "github", "token": "bad-token"},
        )

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "invalid_git_token"


def test_delete_connection_clears_the_credential(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    provider = Mock()
    provider.validate.return_value = {"login": "octocat"}
    with patch("admin.backend.api.v1.git.provider_for_name", return_value=provider):
        client.put("/api/v1/git/connection", json={"provider": "github", "token": "ghp_abc123"})

    response = client.delete("/api/v1/git/connection")
    status = client.get("/api/v1/git/connection").get_json()

    assert response.status_code == 204
    assert response.data == b""
    assert status["connected"] is False


def test_repositories_requires_a_connection(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = client.get("/api/v1/git/repositories")

    assert response.status_code == 401


def test_repositories_returns_the_list_directly(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    provider = Mock()
    provider.validate.return_value = {"login": "octocat"}
    with patch("admin.backend.api.v1.git.provider_for_name", return_value=provider):
        client.put("/api/v1/git/connection", json={"provider": "github", "token": "ghp_abc123"})

    provider.list_repos.return_value = [{"name": "suite"}]
    with patch("admin.backend.api.v1.git.provider_for_name", return_value=provider):
        response = client.get("/api/v1/git/repositories")

    assert response.status_code == 200
    assert response.get_json() == [{"name": "suite"}]


def test_branches_returns_branches_and_default(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    provider = Mock()
    provider.list_branches.return_value = ["main", "develop"]
    provider.get_default_branch.return_value = "main"

    with patch("admin.backend.api.v1.git.provider_for_repo", return_value=provider), \
         patch("admin.backend.api.v1.git.provider_for_name", return_value=provider):
        response = client.get(
            "/api/v1/git/branches", query_string={"repo": "https://github.com/frappe/suite"}
        )

    body = response.get_json()
    assert response.status_code == 200
    assert body == {"branches": ["main", "develop"], "default_branch": "main"}
    assert "ok" not in body


def test_repository_resolutions_returns_the_resolved_app(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    with patch("admin.backend.api.v1.git.provider_for_repo", return_value=Mock()), \
         patch(
             "admin.backend.api.v1.git.resolve_app_name_from_repo",
             return_value={"name": "suite", "description": "A suite app"},
         ):
        response = client.post(
            "/api/v1/git/repository-resolutions",
            json={"repo": "https://github.com/frappe/suite", "branch": "develop"},
        )

    body = response.get_json()
    assert response.status_code == 200
    assert body == {"name": "suite", "description": "A suite app"}
    assert "ok" not in body


def test_repository_resolutions_requires_a_repo(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = client.post("/api/v1/git/repository-resolutions", json={})

    assert response.status_code == 422
