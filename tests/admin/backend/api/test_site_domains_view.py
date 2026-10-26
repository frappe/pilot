"""Tests for /api/v1/sites/<name>/domains item routes and DNS guidance."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

from pilot.config import BenchConfig


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


def _make_site(bench_root: Path, name: str, **config) -> None:
    site_dir = bench_root / "sites" / name
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps(config))


def _mocked_site_domains(bench_root: Path, site: str, domains=(), primary=None):
    from pilot.tasks import TaskRunner
    from pilot.tasks.setup_nginx import SetupNginxTask
    from pilot.utils import normalize_host

    site_domains = Mock()
    domain_names = list(domains)
    site_domains.names.return_value = domain_names
    site_domains.primary.return_value = primary
    site_domains.apply_task.side_effect = lambda: TaskRunner(bench_root).run_task(SetupNginxTask)

    def status(domain: str):
        normalized = normalize_host(domain)
        if normalized == normalize_host(site):
            return True, not primary or normalize_host(primary) == normalized
        attached = normalized in {normalize_host(name) for name in domain_names}
        return attached, bool(primary) and normalize_host(primary) == normalized

    site_domains.status.side_effect = status

    def description(domain: str):
        attached, is_primary = status(domain)
        if not attached:
            return None
        return {
            "domain": domain,
            "is_site": domain == site,
            "is_primary": is_primary,
            "public_scheme": "http",
            "tls": False,
        }

    site_domains.describe_domain.side_effect = description
    effective_primary = primary or site
    site_domains.describe.return_value = (
        [description(domain) for domain in [site, *domain_names]],
        effective_primary,
    )
    return site_domains


def _request(client, method, path, **kwargs):
    with patch(
        "pilot.internal.tasks.runner.task_workers.wake",
        return_value=False,
    ):
        return getattr(client, method)(path, **kwargs)


def test_get_domain_reports_the_site_itself_as_attached_and_primary(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    client = _client(bench_root)

    with patch(
        "admin.backend.api.v1.sites.domains._site_domains",
        return_value=_mocked_site_domains(bench_root, "site.localhost", primary=None),
    ):
        response = client.get("/api/v1/sites/site.localhost/domains/site.localhost")

    assert response.status_code == 200
    assert response.get_json() == {
        "domain": "site.localhost",
        "is_site": True,
        "is_primary": True,
        "public_scheme": "http",
        "tls": False,
    }


def test_list_domains_reports_public_tls_per_hostname(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    route = {
        "public_scheme": "https",
        "origin_scheme": "http",
        "client_ip_source": "x_forwarded_for",
    }
    _make_site(
        bench_root,
        "site.localhost",
        route=route,
        domains=[
            {
                "domain": "custom.example.com",
                "route": {**route, "origin_scheme": "https", "client_ip_source": "proxy_protocol_v2"},
            }
        ],
    )
    client = _client(bench_root)

    response = client.get("/api/v1/sites/site.localhost/domains")

    assert response.status_code == 200
    assert [row["tls"] for row in response.get_json()["domains"]] == [True, True]
    assert response.get_json()["certificate_pending"] is False


def test_get_domain_reports_a_custom_domain(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    client = _client(bench_root)

    with patch(
        "admin.backend.api.v1.sites.domains._site_domains",
        return_value=_mocked_site_domains(
            bench_root,
            "site.localhost",
            domains=["custom.example.com"],
            primary="custom.example.com",
        ),
    ):
        response = client.get("/api/v1/sites/site.localhost/domains/custom.example.com")

    assert response.status_code == 200
    assert response.get_json() == {
        "domain": "custom.example.com",
        "is_site": False,
        "is_primary": True,
        "public_scheme": "http",
        "tls": False,
    }


def test_get_domain_404s_for_an_unattached_domain(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    client = _client(bench_root)

    with patch(
        "admin.backend.api.v1.sites.domains._site_domains",
        return_value=_mocked_site_domains(bench_root, "site.localhost"),
    ):
        response = client.get("/api/v1/sites/site.localhost/domains/other.example.com")

    assert response.status_code == 404


def test_update_domain_sets_primary_and_queues_nginx(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost", domains=["custom.example.com"])
    client = _client(bench_root)
    site_domains = _mocked_site_domains(bench_root, "site.localhost", domains=["custom.example.com"])

    with patch("admin.backend.api.v1.sites.domains._site_domains", return_value=site_domains):
        response = _request(
            client,
            "patch",
            "/api/v1/sites/site.localhost/domains/custom.example.com",
            json={"primary": True},
        )

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "setup-nginx"
    site_domains.set_primary.assert_called_once_with("custom.example.com")


def test_update_domain_rejects_unsupported_fields(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    client = _client(bench_root)

    response = _request(
        client,
        "patch",
        "/api/v1/sites/site.localhost/domains/custom.example.com",
        json={"primary": False},
    )

    assert response.status_code == 422


def test_delete_domain_queues_removal(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost", domains=["custom.example.com"])
    client = _client(bench_root)
    site_domains = _mocked_site_domains(bench_root, "site.localhost", domains=["custom.example.com"])

    with patch("admin.backend.api.v1.sites.domains._site_domains", return_value=site_domains):
        response = _request(client, "delete", "/api/v1/sites/site.localhost/domains/custom.example.com")

    body = response.get_json()
    assert response.status_code == 202
    assert body["command"] == "setup-nginx"
    site_domains.deregister.assert_called_once_with("custom.example.com")


def test_domain_dns_records_is_read_only_and_returns_records_directly(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    client = _client(bench_root)
    site_domains = _mocked_site_domains(bench_root, "site.localhost")
    site_domains.generate_dns_records.return_value = {"cname": [{"type": "CNAME"}], "a": []}

    with patch("admin.backend.api.v1.sites.domains._site_domains", return_value=site_domains):
        response = client.get("/api/v1/sites/site.localhost/domains/custom.example.com/dns-records")

    assert response.status_code == 200
    assert response.get_json() == {"cname": [{"type": "CNAME"}], "a": []}


def test_domain_routes_reject_missing_site(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    assert client.get("/api/v1/sites/missing.localhost/domains/custom.example.com").status_code == 404
    assert (
        _request(client, "delete", "/api/v1/sites/missing.localhost/domains/custom.example.com").status_code
        == 404
    )


def test_adding_a_domain_rejects_an_invalid_certificate_email_before_attaching(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.example.com", ssl=True)
    client = _client(bench_root)

    with patch("pilot.core.site.domains.SiteDomains.register") as register:
        response = client.post(
            "/api/v1/sites/site.example.com/domains",
            json={"domain": "shop.example.com", "email": "not-an-email"},
        )

    assert response.status_code == 422
    assert response.get_json()["error"]["details"] == {"needs_email": True}
    register.assert_not_called()


def test_adding_a_domain_with_an_email_passes_it_to_the_certificate_task(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.example.com", ssl=True)
    client = _client(bench_root)

    with (
        patch("pilot.core.site.domains.SiteDomains.register"),
        patch("pilot.core.site.domains.SiteDomains.apply_task", return_value="task-1") as apply_task,
        patch("admin.backend.api.v1.sites.domains.accepted_task_response", return_value=("", 202)),
    ):
        response = client.post(
            "/api/v1/sites/site.example.com/domains",
            json={"domain": "shop.example.com", "email": "ops@example.com"},
        )

    assert response.status_code == 202
    apply_task.assert_called_once_with(email="ops@example.com")


def test_attaching_a_primary_domain_keeps_the_site_directory_and_can_be_retried(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.local", domains=["shop.example.com"])
    client = _client(bench_root)
    with (
        patch("pilot.core.site.domains.SiteDomains.apply_task", return_value="task-1"),
        patch("admin.backend.api.v1.sites.domains.accepted_task_response", return_value=("", 202)),
    ):
        for _ in range(2):
            response = client.post(
                "/api/v1/sites/site.local/domains", json={"domain": "shop.example.com", "primary": True}
            )
            assert response.status_code == 202
    config = json.loads((bench_root / "sites/site.local/site_config.json").read_text())
    assert config["domains"] == ["shop.example.com"]
    assert config["host_name"] == "http://shop.example.com"
    assert not (bench_root / "sites/shop.example.com").exists()


def test_primary_domain_flag_requires_a_boolean(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.local")
    response = _client(bench_root).post(
        "/api/v1/sites/site.local/domains", json={"domain": "shop.example.com", "primary": "true"}
    )
    assert response.status_code == 422
