"""Tests for /api/v1/git/connection, /repositories, /branches, /repository-resolutions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from pilot.config import BenchConfig
from pilot.integrations.central.client import CentralClientError
from pilot.integrations.git import BranchNotFoundError, GitAuthError


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

    with (
        patch("admin.backend.api.v1.git.provider_for_repo", return_value=provider),
        patch("admin.backend.api.v1.git.provider_for_name", return_value=provider),
    ):
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

    with (
        patch("admin.backend.api.v1.git.provider_for_repo", return_value=Mock()),
        patch(
            "admin.backend.api.v1.git.resolve_app_name_from_repo",
            return_value={"name": "suite", "description": "A suite app"},
        ),
    ):
        response = client.post(
            "/api/v1/git/repository-resolutions",
            json={"repo": "https://github.com/frappe/suite", "branch": "develop"},
        )

    body = response.get_json()
    assert response.status_code == 200
    assert body == {"name": "suite", "description": "A suite app"}
    assert "ok" not in body


def test_repository_resolutions_rejects_an_unknown_branch(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    with (
        patch("admin.backend.api.v1.git.provider_for_repo", return_value=Mock()),
        patch(
            "admin.backend.api.v1.git.resolve_app_name_from_repo",
            side_effect=BranchNotFoundError("'missing' is not a branch of this repository."),
        ),
    ):
        response = client.post(
            "/api/v1/git/repository-resolutions",
            json={"repo": "https://github.com/frappe/frappe", "branch": "missing"},
        )

    error = response.get_json()["error"]
    assert response.status_code == 422
    assert error["code"] == "branch_not_found"


def test_repository_resolutions_requires_a_repo(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = client.post("/api/v1/git/repository-resolutions", json={})

    assert response.status_code == 422


def test_put_connection_refuses_a_token_while_central_grants_github(tmp_path: Path) -> None:
    from pilot.integrations.git import GitCredentialStore

    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    GitCredentialStore(bench_root).save_github_app("octocat")

    response = client.put("/api/v1/git/connection", json={"provider": "github", "token": "ghp_abc123"})

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "github_app_connected"


def test_a_rejected_repository_token_keeps_the_github_app_grant(tmp_path: Path) -> None:
    from pilot.integrations.git import GitCredentialStore

    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    store = GitCredentialStore(bench_root)
    store.save_github_app("octocat")
    provider = Mock()
    provider.list_branches.side_effect = GitAuthError()

    with (
        patch("admin.backend.api.v1.git.token_for", return_value="ghs_repository"),
        patch("admin.backend.api.v1.git.provider_for_repo", return_value=provider),
    ):
        response = client.get("/api/v1/git/branches", query_string={"repo": "https://github.com/octocat/app"})

    assert response.status_code == 401
    assert store.load()["is_token_valid"] is True


def test_starting_a_central_connection_reports_an_unreachable_central(tmp_path: Path) -> None:
    from pilot.config.common import CommonConfig

    client = _client(tmp_path / "benches" / "current")
    with CommonConfig.open(tmp_path / "benches") as common:
        common.central.enabled = True
        common.central.bootstrapped = True

    with patch(
        "pilot.integrations.central.client.CentralClient.start_github_connection",
        side_effect=CentralClientError("down"),
    ):
        response = client.post(
            "/api/v1/git/connection-attempts", json={"return_url": "https://bench.test/git"}
        )

    assert response.status_code == 502
    assert response.get_json()["error"]["code"] == "central_unavailable"


def test_disconnect_keeps_the_github_app_grant_when_central_is_unreachable(tmp_path: Path) -> None:
    from pilot.integrations.git import GitCredentialStore

    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    store = GitCredentialStore(bench_root)
    store.save_github_app("octocat")

    with patch(
        "pilot.integrations.central.client.CentralClient.disconnect_github",
        side_effect=CentralClientError("down"),
    ):
        response = client.delete("/api/v1/git/connection")

    assert response.status_code == 502
    assert store.load()["username"] == "octocat"


def test_starting_a_central_connection_returns_the_central_link(tmp_path: Path) -> None:
    from pilot.config.common import CommonConfig

    client = _client(tmp_path / "benches" / "current")
    with CommonConfig.open(tmp_path / "benches") as common:
        common.central.enabled = True
        common.central.bootstrapped = True

    with patch(
        "pilot.integrations.central.client.CentralClient.start_github_connection",
        return_value="https://central.test/dashboard/github/connect/attempt",
    ) as start:
        response = client.post(
            "/api/v1/git/connection-attempts", json={"return_url": "https://bench.test/git"}
        )

    assert response.get_json() == {"url": "https://central.test/dashboard/github/connect/attempt"}
    start.assert_called_once_with("https://bench.test/git")


def test_disconnect_removes_the_github_app_grant_on_central(tmp_path: Path) -> None:
    from pilot.integrations.git import GitCredentialStore

    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    GitCredentialStore(bench_root).save_github_app("octocat")

    with patch("pilot.integrations.central.client.CentralClient.disconnect_github") as disconnect:
        response = client.delete("/api/v1/git/connection")

    assert response.status_code == 204
    disconnect.assert_called_once_with()
    assert GitCredentialStore(bench_root).load() is None
