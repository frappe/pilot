"""Tests for /api/v1/sites/<name>/domains item routes and DNS guidance."""
from __future__ import annotations

import json
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


def _make_site(bench_root: Path, name: str, **config) -> None:
    site_dir = bench_root / "sites" / name
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps(config))


def _mocked_routes(domains=(), primary=None):
    routes = Mock()
    routes.domains.return_value = list(domains)
    routes.primary.return_value = primary
    return routes


def _request(client, method, path, **kwargs):
    with patch(
        "pilot.tasks.manager.task_runner.task_workers.wake",
        return_value=False,
    ):
        return getattr(client, method)(path, **kwargs)


def test_get_domain_reports_the_site_itself_as_attached_and_primary(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    client = _client(bench_root)

    with patch(
        "admin.backend.api.v1.sites.domains._domain_routes",
        return_value=_mocked_routes(primary=None),
    ):
        response = client.get("/api/v1/sites/site.localhost/domains/site.localhost")

    assert response.status_code == 200
    assert response.get_json() == {"domain": "site.localhost", "is_primary": True}


def test_get_domain_reports_a_custom_domain(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    client = _client(bench_root)

    with patch(
        "admin.backend.api.v1.sites.domains._domain_routes",
        return_value=_mocked_routes(domains=["custom.example.com"], primary="custom.example.com"),
    ):
        response = client.get("/api/v1/sites/site.localhost/domains/custom.example.com")

    assert response.status_code == 200
    assert response.get_json() == {"domain": "custom.example.com", "is_primary": True}


def test_get_domain_404s_for_an_unattached_domain(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    client = _client(bench_root)

    with patch(
        "admin.backend.api.v1.sites.domains._domain_routes",
        return_value=_mocked_routes(),
    ):
        response = client.get("/api/v1/sites/site.localhost/domains/other.example.com")

    assert response.status_code == 404


def test_update_domain_sets_primary_and_queues_nginx(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost", domains=["custom.example.com"])
    client = _client(bench_root)
    routes = _mocked_routes(domains=["custom.example.com"])

    with patch("admin.backend.api.v1.sites.domains._domain_routes", return_value=routes):
        response = _request(
            client, "patch",
            "/api/v1/sites/site.localhost/domains/custom.example.com",
            json={"primary": True},
        )

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "setup-nginx"
    routes.set_primary.assert_called_once_with("site.localhost", "custom.example.com")


def test_update_domain_rejects_unsupported_fields(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    client = _client(bench_root)

    response = _request(
        client, "patch",
        "/api/v1/sites/site.localhost/domains/custom.example.com",
        json={"primary": False},
    )

    assert response.status_code == 422


def test_delete_domain_queues_removal(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost", domains=["custom.example.com"])
    client = _client(bench_root)
    routes = _mocked_routes(domains=["custom.example.com"])

    with patch("admin.backend.api.v1.sites.domains._domain_routes", return_value=routes):
        response = _request(client, "delete", "/api/v1/sites/site.localhost/domains/custom.example.com")

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "setup-nginx"
    routes.deregister.assert_called_once_with("site.localhost", "custom.example.com")


def test_domain_dns_records_is_read_only_and_returns_records_directly(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    client = _client(bench_root)
    routes = _mocked_routes()
    routes.generate_dns_records.return_value = {"cname": [{"type": "CNAME"}], "a": []}

    with patch("admin.backend.api.v1.sites.domains._domain_routes", return_value=routes):
        response = client.get("/api/v1/sites/site.localhost/domains/custom.example.com/dns-records")

    assert response.status_code == 200
    assert response.get_json() == {"cname": [{"type": "CNAME"}], "a": []}


def test_domain_routes_reject_missing_site(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    assert client.get("/api/v1/sites/missing.localhost/domains/custom.example.com").status_code == 404
    assert _request(
        client, "delete", "/api/v1/sites/missing.localhost/domains/custom.example.com"
    ).status_code == 404
