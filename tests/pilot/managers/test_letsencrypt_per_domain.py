from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config import RoutePolicy, SiteConfig
from pilot.managers.letsencrypt import (
    LetsEncryptManager,
    certificate_domains,
    is_public_domain,
    public_domains,
)
from tests.pilot.integrations.test_central_client import _bench


def _site(bench, name: str, **config) -> None:
    site_dir = bench.sites_path / name
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site_config.json").write_text(json.dumps(config))


def _obtained(bench) -> list[str]:
    """The sites obtain_all actually asked certbot for."""
    names: list[str] = []
    with (
        patch.object(LetsEncryptManager, "obtain", lambda self, site: names.append(site.name)),
        patch.object(LetsEncryptManager, "obtain_admin", lambda self: names.append("ADMIN")),
    ):
        LetsEncryptManager(bench).obtain_all()
    return names


def test_certificate_domains_are_only_those_terminating_here() -> None:
    site = SiteConfig(
        name="site-a1.zone.example",
        apps=[],
        domains=[{"domain": "shop.customer.com", "tls": True}],
        ssl=False,
    )

    # Both are candidates for TLS; only the custom domain terminates here.
    assert public_domains(site) == ["site-a1.zone.example", "shop.customer.com"]
    assert certificate_domains(site) == ["shop.customer.com"]


def test_edge_tls_site_does_not_get_an_origin_certificate() -> None:
    site = SiteConfig(
        name="site-a1.zone.example",
        apps=[],
        ssl=True,
        route=RoutePolicy("https", "http", "x_forwarded_for"),
    )

    assert public_domains(site) == ["site-a1.zone.example"]
    assert certificate_domains(site) == []


@pytest.mark.parametrize(
    "domain",
    ["local", "site.local", "localhost", "site.localhost", "SITE.LOCAL."],
)
def test_local_names_are_not_public_certificate_domains(domain: str) -> None:
    assert is_public_domain(domain) is False


def test_a_custom_domain_gets_a_certificate_though_the_admin_is_proxied(tmp_path: Path) -> None:
    """The documented cloud case: admin.tls off, site ssl off, one custom domain
    whose TLS ends on this host."""
    bench = _bench(tmp_path)
    bench.config.admin.tls = False
    _site(bench, "site-a1.zone.example", ssl=False, domains=[{"domain": "shop.customer.com", "tls": True}])

    assert _obtained(bench) == ["site-a1.zone.example"]


def test_a_plain_site_is_left_alone(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    bench.config.admin.tls = False
    _site(bench, "site-a1.zone.example", ssl=False)

    assert _obtained(bench) == []


def test_the_admin_certificate_still_follows_the_admin_flag(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    bench.config.admin.domain = "admin.example.com"

    bench.config.admin.tls = False
    assert "ADMIN" not in _obtained(bench)

    bench.config.admin.tls = True
    assert "ADMIN" in _obtained(bench)


def test_a_failure_names_the_certificate_domain_not_only_the_site(tmp_path: Path, capsys) -> None:
    """Certbot ran for the custom domain, so the report must name that domain."""
    from pilot.exceptions import CommandError

    bench = _bench(tmp_path)
    bench.config.admin.tls = False
    _site(bench, "site-a1.zone.example", ssl=False, domains=[{"domain": "shop.customer.com", "tls": True}])

    def _fail(self, site) -> None:
        raise CommandError("certbot failed")

    with patch.object(LetsEncryptManager, "obtain", _fail):
        LetsEncryptManager(bench).obtain_all()

    output = capsys.readouterr().out
    assert "'shop.customer.com'" in output
    assert "site 'site-a1.zone.example'" in output
