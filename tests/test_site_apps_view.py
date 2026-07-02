"""Tests for GET /api/sites/<name>/apps resolving title/description per app."""
from __future__ import annotations

import json
from pathlib import Path

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


def test_site_apps_includes_title_and_description(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["suite"])
    _make_app(bench_root, "suite", '[project]\nname = "suite"\ndescription = "A custom suite app"\n')

    client = _client(bench_root)
    response = client.get("/api/sites/site1.localhost/apps")

    assert response.status_code == 200
    apps = {app["name"]: app for app in response.get_json()["apps"]}
    assert apps["suite"]["title"] == "suite"
    assert apps["suite"]["description"] == "A custom suite app"


def test_site_apps_falls_back_to_name_when_app_missing(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site1.localhost", ["ghost"])

    client = _client(bench_root)
    response = client.get("/api/sites/site1.localhost/apps")

    assert response.status_code == 200
    apps = {app["name"]: app for app in response.get_json()["apps"]}
    assert apps["ghost"]["title"] == "ghost"
    assert apps["ghost"]["description"] == ""
