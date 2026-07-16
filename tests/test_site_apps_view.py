"""Tests for /api/v1/sites/<name>/apps: listing, install, and uninstall."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from pilot.config.bench_toml_builder import BenchTomlBuilder


def _write_bench_toml(bench_dir: Path, name: str, **settings) -> None:
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text(BenchTomlBuilder(name, settings).render())


def _client(bench_root: Path, password: str = "secret"):
    from admin.backend.app import create_app
    from pilot.commands.generate_session import ensure_jwt_secret, issue_token

    _write_bench_toml(bench_root, bench_root.name, admin_enabled=True, admin_password=password)
    secret = ensure_jwt_secret(bench_root / "bench.toml")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", issue_token(secret))
    return client


def _make_site(bench_root: Path, name: str, installed_apps: list[str]) -> None:
    site_dir = bench_root / "sites" / name
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps({"installed_apps": installed_apps}))


def _make_app(bench_root: Path, name: str, pyproject: str) -> None:
    app_dir = bench_root / "apps" / name
    app_dir.mkdir(parents=True)
    (app_dir / "pyproject.toml").write_text(pyproject)


def _make_cloned_app(bench_root: Path, name: str) -> None:
    app_dir = bench_root / "apps" / name
    app_dir.mkdir(parents=True)
    (app_dir / ".git").mkdir()


def test_site_apps_includes_title_and_description(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["suite"])
    _make_app(bench_root, "suite", '[project]\nname = "suite"\ndescription = "A custom suite app"\n')

    client = _client(bench_root)
    response = client.get("/api/v1/sites/site1.localhost/apps")

    assert response.status_code == 200
    apps = {app["name"]: app for app in response.get_json()["apps"]}
    assert apps["suite"]["title"] == "suite"
    assert apps["suite"]["description"] == "A custom suite app"


def test_site_apps_falls_back_to_name_when_app_missing(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["ghost"])

    client = _client(bench_root)
    response = client.get("/api/v1/sites/site1.localhost/apps")

    assert response.status_code == 200
    apps = {app["name"]: app for app in response.get_json()["apps"]}
    assert apps["ghost"]["title"] == "ghost"
    assert apps["ghost"]["description"] == ""


def _post_install(client, site: str, **payload):
    with patch(
        "pilot.tasks.manager.task_runner.task_workers.wake",
        return_value=False,
    ):
        return client.post(f"/api/v1/sites/{site}/apps", json=payload)


def _delete_app(client, site: str, app: str, **query):
    with patch(
        "pilot.tasks.manager.task_runner.task_workers.wake",
        return_value=False,
    ):
        return client.delete(f"/api/v1/sites/{site}/apps/{app}", query_string=query)


def test_install_app_uses_fast_path_when_already_cloned(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", [])
    _make_cloned_app(bench_root, "suite")
    client = _client(bench_root)

    response = _post_install(client, "site1.localhost", app="suite")

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "install-app"
    assert body["args"] == {"site": "site1.localhost", "app": "suite"}


def test_install_app_fetches_by_repo_when_not_cloned(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", [])
    client = _client(bench_root)

    response = _post_install(
        client,
        "site1.localhost",
        app="suite",
        repo="https://github.com/frappe/suite",
        branch="develop",
    )

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "get-and-install-app"
    assert body["args"]["repo"] == "https://github.com/frappe/suite"
    assert body["args"]["branch"] == "develop"


def test_install_app_treats_bare_name_as_marketplace_when_not_cloned(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", [])
    client = _client(bench_root)

    response = _post_install(client, "site1.localhost", app="suite")

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "get-and-install-app"
    assert body["args"]["marketplace_app"] == "suite"


def test_install_app_requires_app_or_repo(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", [])
    client = _client(bench_root)

    response = _post_install(client, "site1.localhost")

    assert response.status_code == 422


def test_install_app_rejects_missing_site(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = _post_install(client, "missing.localhost", app="suite")

    assert response.status_code == 404


def test_delete_site_app_queues_uninstall(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["suite"])
    client = _client(bench_root)

    response = _delete_app(client, "site1.localhost", "suite")

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "uninstall-app"
    assert body["args"] == {"site": "site1.localhost", "app": "suite", "force": False}


def test_delete_site_app_passes_force_flag(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["suite"])
    client = _client(bench_root)

    response = _delete_app(client, "site1.localhost", "suite", force="true")

    body = response.get_json()
    assert body["args"]["force"] is True


def test_delete_site_app_rejects_invalid_app_name(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", [])
    client = _client(bench_root)

    response = _delete_app(client, "site1.localhost", "bad.app")

    assert response.status_code == 422


def test_delete_site_app_rejects_missing_site(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    response = _delete_app(client, "missing.localhost", "suite")

    assert response.status_code == 404
