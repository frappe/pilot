from pathlib import Path

from pilot.config import BenchConfig, SiteConfig
from pilot.core.bench import Bench
from pilot.managers.nginx import NginxConfigRenderer

_DATA: dict = {
    "bench": {"name": "self-hosted", "python": "3.14"},
    "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
    "redis": {"cache_port": 13000, "queue_port": 11000},
    "admin": {"domain": "admin.example.com", "tls": True},
}


def _render(tmp_path: Path, site: SiteConfig, tls_domains: list[str]) -> str:
    renderer = NginxConfigRenderer(Bench(BenchConfig._from_dict(_DATA), tmp_path))
    renderer._proxy_servers_cache = []
    return renderer.generate_bench_config([(site, tls_domains)], admin_ssl=True)


def test_central_defaults_to_off() -> None:
    assert BenchConfig._from_dict(_DATA).central.enabled is False


def test_no_vm_aliases_without_central(tmp_path: Path) -> None:
    site = SiteConfig(name="site1.example.com", apps=["frappe"])
    config = _render(tmp_path, site, [])

    assert "~^" not in config  # the alias server_name is the only regex vhost


def test_proxy_protocol_is_off_by_default(tmp_path: Path) -> None:
    site = SiteConfig(name="site1.example.com", apps=["frappe"], ssl=True)
    config = _render(tmp_path, site, site.all_domains)

    assert "listen 443 ssl http2;" in config
    assert "proxy_protocol;" not in config


def test_an_ssl_site_still_serves_every_domain_over_https(tmp_path: Path) -> None:
    """The pre-existing whole-site `ssl` flag keeps its meaning."""
    site = SiteConfig(name="site1.example.com", apps=["frappe"], domains=["www.example.com"], ssl=True)

    assert site.tls_domains == ["site1.example.com", "www.example.com"]
    assert site.plain_domains == []

    config = _render(tmp_path, site, site.tls_domains)
    assert "server_name site1.example.com www.example.com;" in config
    assert "return 301 https://$host$request_uri" in config


def test_a_plain_site_stays_http(tmp_path: Path) -> None:
    site = SiteConfig(name="site1.example.com", apps=["frappe"], domains=["www.example.com"])

    assert site.tls_domains == []
    config = _render(tmp_path, site, [])

    assert "listen 80;" in config
    assert "ssl_certificate" not in config.split("server_name admin.example.com")[0]
