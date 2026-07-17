"""Tests for /api/v1/apps, /api/v1/marketplace/apps, and /api/v1/app-updates."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

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


def _make_cloned_app(bench_root: Path, name: str) -> None:
    app_dir = bench_root / "apps" / name
    app_dir.mkdir(parents=True)
    (app_dir / ".git").mkdir()


def _post_install(client, **payload):
    with patch(
        "pilot.tasks.manager.task_runner.task_workers.wake",
        return_value=False,
    ):
        return client.post("/api/v1/apps", json=payload)


def test_install_rejects_an_already_cloned_marketplace_app(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_cloned_app(bench_root, "suite")
    client = _client(bench_root)

    response = _post_install(client, name="suite")

    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "app_already_installed"


def test_install_fetches_by_repo_without_sites(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = _post_install(client, repo="https://github.com/frappe/suite", branch="develop")

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "get-app"
    assert body["args"]["repo"] == "https://github.com/frappe/suite"
    assert body["args"]["branch"] == "develop"


def test_install_fetches_and_installs_by_repo_onto_sites(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = _post_install(
        client, repo="https://github.com/frappe/suite", sites=["s1.localhost", "s1.localhost"]
    )

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "get-and-install-app"
    assert body["args"]["repo"] == "https://github.com/frappe/suite"
    assert body["args"]["sites"] == ["s1.localhost"]


def test_install_fetches_and_installs_a_marketplace_app_onto_sites(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = _post_install(client, name="suite", sites=["s1.localhost"])

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "get-and-install-app"
    assert body["args"]["marketplace_app"] == "suite"
    assert body["args"]["sites"] == ["s1.localhost"]


def test_install_requires_a_name_or_repo(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = _post_install(client)

    assert response.status_code == 422


def test_install_rejects_an_invalid_repo_url(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = _post_install(client, repo="not-a-url")

    assert response.status_code == 422


def test_get_app_returns_the_cloned_app(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_cloned_app(bench_root, "suite")
    client = _client(bench_root)

    response = client.get("/api/v1/apps/suite")

    assert response.status_code == 200
    assert response.get_json()["name"] == "suite"


def test_get_app_404s_when_not_cloned(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = client.get("/api/v1/apps/suite")

    assert response.status_code == 404


def test_update_app_sets_the_upstream_remote(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_cloned_app(bench_root, "suite")
    client = _client(bench_root)

    with patch("admin.backend.api.v1.apps.GitRepo") as git_repo:
        git_repo.return_value.set_remote_url.return_value = True
        response = client.patch(
            "/api/v1/apps/suite", json={"repo": "https://github.com/frappe/suite"}
        )

    assert response.status_code == 200
    assert response.get_json()["name"] == "suite"
    git_repo.return_value.set_remote_url.assert_called_once_with("https://github.com/frappe/suite")


def test_update_app_404s_when_not_cloned(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = client.patch(
        "/api/v1/apps/suite", json={"repo": "https://github.com/frappe/suite"}
    )

    assert response.status_code == 404


def test_delete_app_queues_removal(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_cloned_app(bench_root, "suite")
    client = _client(bench_root)

    with patch(
        "pilot.tasks.manager.task_runner.task_workers.wake",
        return_value=False,
    ):
        response = client.delete("/api/v1/apps/suite")

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "remove-app"
    assert body["args"] == {"name": "suite"}


def test_delete_app_404s_when_missing(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = client.delete("/api/v1/apps/suite")

    assert response.status_code == 404


def test_marketplace_returns_catalog_apps(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    marketplace = Mock()
    marketplace.read_all_apps.return_value = [Mock(to_dict=lambda: {"app": "suite"})]

    with patch("pilot.integrations.marketplace.Marketplace", return_value=marketplace):
        response = client.get("/api/v1/marketplace/apps")

    assert response.status_code == 200
    assert response.get_json() == [{"app": "suite"}]


def test_app_updates_reads_without_fetching(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    app = Mock(is_cloned=True)
    app.config.name = "suite"
    app.config.branch = "develop"
    bench = Mock()
    bench.apps.return_value = [app]
    repo = Mock()

    with patch("pilot.core.bench.Bench", return_value=bench), \
         patch("admin.backend.api.v1.updates.GitRepo", return_value=repo), \
         patch("admin.backend.api.v1.updates._app_info", return_value={"name": "suite"}):
        response = client.get("/api/v1/app-updates")

    assert response.status_code == 200
    assert response.get_json() == {"apps": [{"name": "suite"}]}
    repo.fetch.assert_not_called()


def test_app_update_checks_fetches_each_cloned_app(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    app = Mock(is_cloned=True)
    app.config.name = "suite"
    app.config.branch = "develop"
    bench = Mock()
    bench.apps.return_value = [app]
    repo = Mock()

    with patch("pilot.core.bench.Bench", return_value=bench), \
         patch("admin.backend.api.v1.updates.GitRepo", return_value=repo), \
         patch("admin.backend.api.v1.updates._app_info", return_value={"name": "suite"}):
        response = client.post("/api/v1/app-update-checks")

    assert response.status_code == 200
    assert response.get_json() == {"apps": [{"name": "suite"}]}
    repo.fetch.assert_called_once_with("develop", timeout=60)
