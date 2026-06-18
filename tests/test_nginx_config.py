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


_ADMIN_SYSTEMD_DATA: dict = {
    **_BASE_DATA,
    "production": {"process_manager": "systemd", "nginx": True},
    "admin": {"enabled": True, "port": 8002, "password": "x"},
}


def test_admin_domainless_proxy_under_systemd(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _ADMIN_SYSTEMD_DATA)
    bench.create_directories()
    (tmp_path / "sites" / "site1.example.com").mkdir(parents=True)
    (tmp_path / "sites" / "site1.example.com" / "site_config.json").write_text("{}")

    manager = NginxManager(bench)
    manager.generate_config(ssl_ready=False)

    admin_conf = tmp_path / "config" / "nginx" / "sites" / "_admin.conf"
    assert admin_conf.exists()
    content = admin_conf.read_text()
    assert "server_name _;" in content
    assert "listen 8002;" in content
    assert "listen [::]:8002;" in content
    # Forwards to the socket-activated gunicorn on the internal port, not admin.port.
    assert f"proxy_pass         http://127.0.0.1:{bench.config.admin.internal_port};" in content


def test_admin_no_domainless_proxy_without_systemd(tmp_path: Path) -> None:
    data = copy.deepcopy(_ADMIN_SYSTEMD_DATA)
    data["production"]["process_manager"] = "supervisor"
    bench = _make_bench(tmp_path, data)
    bench.create_directories()
    (tmp_path / "sites" / "site1.example.com").mkdir(parents=True)
    (tmp_path / "sites" / "site1.example.com" / "site_config.json").write_text("{}")

    manager = NginxManager(bench)
    manager.generate_config(ssl_ready=False)

    assert not (tmp_path / "config" / "nginx" / "sites" / "_admin.conf").exists()


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
    assert "listen [::]:8080;" in config


# ── IPv6 (dual-stack listeners) ───────────────────────────────────────────────


def test_http_site_listens_dual_stack(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = NginxManager(bench)

    config = manager._generate_site_config(_BASE_SITE, ssl_ready=False)

    assert "listen 80;" in config
    assert "listen [::]:80;" in config


def test_https_site_listens_dual_stack(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _SSL_DATA)
    manager = NginxManager(bench)

    config = manager._generate_site_config(_SSL_SITE, ssl_ready=True)

    # HTTP→HTTPS redirect block and the SSL block both listen on both stacks.
    assert "listen 80;" in config
    assert "listen [::]:80;" in config
    assert "listen 443 ssl http2;" in config
    assert "listen [::]:443 ssl http2;" in config


def test_admin_domain_config_listens_dual_stack(tmp_path: Path) -> None:
    data = copy.deepcopy(_SSL_DATA)
    data["admin"] = {"enabled": True, "domain": "admin.example.com"}
    bench = _make_bench(tmp_path, data)
    manager = NginxManager(bench)

    config = manager._generate_admin_config(ssl_ready=False)

    assert "listen 80;" in config
    assert "listen [::]:80;" in config


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


def test_socketio_location_proxies_to_socketio_port(tmp_path: Path) -> None:
    data = copy.deepcopy(_BASE_DATA)
    data["bench"] = {"name": "test-bench", "python": "3.14", "socketio_port": 9000}
    bench = _make_bench(tmp_path, data)
    manager = NginxManager(bench)

    config = manager._generate_site_config(_BASE_SITE, ssl_ready=False)

    assert "location /socket.io" in config
    assert "proxy_pass         http://127.0.0.1:9000;" in config
    assert "proxy_set_header   Upgrade $http_upgrade;" in config
