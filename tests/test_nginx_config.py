"""Tests for NginxManager config generation — no real nginx required."""
import copy
from pathlib import Path

import pytest

from bench_cli.config.bench_config import BenchConfig
from bench_cli.config.site_config import SiteConfig
from bench_cli.core.bench import Bench
from bench_cli.managers.nginx_manager import NginxManager


_BASE_DATA: dict = {
    "bench": {"name": "test-bench", "python": "3.14"},
    "apps": [
        {"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}
    ],
    "mariadb": {"root_password": "root"},
    "redis": {"cache_port": 13000, "queue_port": 11000},
}

_SSL_DATA: dict = {
    **_BASE_DATA,
    "nginx": {"enabled": True, "http_port": 80, "https_port": 443},
    "letsencrypt": {"email": "admin@example.com"},
}

_BASE_SITE = SiteConfig(name="site1.example.com", apps=["frappe"])
_SSL_SITE = SiteConfig(name="site1.example.com", apps=["frappe"], ssl=True)


def _make_bench(tmp_path: Path, data: dict) -> Bench:
    config = BenchConfig._from_dict(data)
    return Bench(config, tmp_path)


def test_http_only_site_config(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = NginxManager(bench)

    config = manager._generate_site_config(_BASE_SITE, ssl_ready=False)

    assert "server_name" in config
    assert "listen 80" in config
    assert "ssl_certificate" not in config


def test_ssl_site_not_ready_is_http_only(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _SSL_DATA)
    manager = NginxManager(bench)

    config = manager._generate_site_config(_SSL_SITE, ssl_ready=False)

    assert "listen 80" in config
    assert "ssl_certificate" not in config
    assert "listen 443" not in config


def test_ssl_site_ready_has_https_block(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _SSL_DATA)
    manager = NginxManager(bench)

    config = manager._generate_site_config(_SSL_SITE, ssl_ready=True)

    assert "listen 443 ssl http2" in config
    assert "ssl_certificate" in config
    assert "ssl_certificate_key" in config
    assert "return 301 https://$host$request_uri" in config


def test_include_conf_content(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    bench.create_directories()

    # Place a fake site on disk so generate_config has something to iterate
    site_dir = tmp_path / "sites" / "site1.example.com"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text("{}")

    manager = NginxManager(bench)
    manager.generate_config(ssl_ready=False)

    include_conf = tmp_path / "config" / "nginx" / "include.conf"
    assert include_conf.exists()
    content = include_conf.read_text()
    assert "include" in content
    assert "*.conf" in content
    nginx_dir = str(tmp_path / "config" / "nginx")
    assert nginx_dir in content


def test_server_name_includes_all_domains(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = NginxManager(bench)

    site = SiteConfig(
        name="site1.example.com",
        apps=["frappe"],
        domains=["www.site1.example.com"],
    )
    config_text = manager._generate_site_config(site, ssl_ready=False)

    assert "site1.example.com" in config_text
    assert "www.site1.example.com" in config_text


def test_proxy_headers_present(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = NginxManager(bench)

    config = manager._generate_site_config(_BASE_SITE, ssl_ready=False)

    assert "X-Frappe-Site-Name" in config
    assert "X-Forwarded-Proto" in config


def test_http_port_is_configurable(tmp_path: Path) -> None:
    data = copy.deepcopy(_BASE_DATA)
    data["nginx"] = {"enabled": True, "http_port": 8080, "https_port": 8443}
    bench = _make_bench(tmp_path, data)
    manager = NginxManager(bench)

    config = manager._generate_site_config(_BASE_SITE, ssl_ready=False)

    assert "listen 8080;" in config
    assert "listen 80;" not in config


# ── upstream block ────────────────────────────────────────────────────────────


def test_upstream_block_uses_bench_http_port(tmp_path: Path) -> None:
    """Regression: the upstream block used to hardcode 127.0.0.1:8000
    regardless of the bench's actual http_port."""
    data = copy.deepcopy(_BASE_DATA)
    data["bench"]["http_port"] = 8001
    bench = _make_bench(tmp_path, data)
    manager = NginxManager(bench)

    upstream = manager._render_upstream_block(bench.config.name)

    assert "server 127.0.0.1:8001;" in upstream
    assert "8000" not in upstream
