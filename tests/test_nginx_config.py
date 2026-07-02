"""Tests for NginxManager config generation — no real nginx required."""
import copy
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config.bench_config import BenchConfig
from pilot.config.site_config import SiteConfig
from pilot.core.bench import Bench
from pilot.exceptions import CommandError
from pilot.managers.nginx_manager import NginxManager


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
    "admin": {"enabled": True, "port": 7000, "password": "x", "domain": "admin.example.com"},
}


def test_admin_domain_proxy_under_systemd(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _ADMIN_SYSTEMD_DATA)
    bench.create_directories()
    (tmp_path / "sites" / "site1.example.com").mkdir(parents=True)
    (tmp_path / "sites" / "site1.example.com" / "site_config.json").write_text("{}")

    manager = NginxManager(bench)
    manager.generate_config(ssl_ready=False)

    admin_conf = tmp_path / "config" / "nginx" / "sites" / "_admin.conf"
    assert admin_conf.exists()
    content = admin_conf.read_text()
    assert "server_name admin.example.com;" in content
    # Under systemd the admin is socket-activated on the internal port.
    assert f"proxy_pass         http://127.0.0.1:{bench.config.admin.internal_port};" in content


def test_localhost_ssl_site_gets_https_when_cert_present(tmp_path: Path) -> None:
    # A pure-.localhost SSL site has no public domains to validate a SAN against,
    # so cert existence alone enables HTTPS (the e2e suite runs on site1.localhost).
    data = copy.deepcopy(_SSL_DATA)
    data["admin"] = {"domain": "admin.example.com", "tls": True}
    bench = _make_bench(tmp_path, data)
    bench.create_directories()
    (tmp_path / "sites" / "site1.localhost").mkdir(parents=True)
    (tmp_path / "sites" / "site1.localhost" / "site_config.json").write_text('{"ssl": true}')

    manager = NginxManager(bench)
    manager.cert_exists = lambda site: True  # pretend a cert is present
    manager.generate_config(ssl_ready=True)

    content = (tmp_path / "config" / "nginx" / "sites" / "site1.localhost.conf").read_text()
    assert "listen 443 ssl http2" in content
    assert "return 301 https://$host$request_uri;" in content


def test_admin_tls_disabled_serves_sites_http_only(tmp_path: Path) -> None:
    # admin.tls = False is bench-wide: even an SSL site with a cert on disk is
    # served plain-HTTP, because a central proxy terminates TLS upstream.
    data = copy.deepcopy(_SSL_DATA)
    data["admin"] = {"domain": "admin.example.com", "tls": False}
    bench = _make_bench(tmp_path, data)
    bench.create_directories()
    (tmp_path / "sites" / "site1.example.com").mkdir(parents=True)
    (tmp_path / "sites" / "site1.example.com" / "site_config.json").write_text('{"ssl": true}')

    manager = NginxManager(bench)
    manager.cert_exists = lambda site: True  # pretend a cert is present
    manager.generate_config(ssl_ready=True)

    content = (tmp_path / "config" / "nginx" / "sites" / "site1.example.com.conf").read_text()
    assert "listen 80" in content
    assert "ssl_certificate" not in content
    assert "return 301 https://" not in content


def test_admin_tls_disabled_serves_plain_http(tmp_path: Path) -> None:
    # With admin.tls = False a central proxy terminates TLS; nginx must serve the
    # admin over plain HTTP on :80 and never redirect to HTTPS.
    data = copy.deepcopy(_ADMIN_SYSTEMD_DATA)
    data["admin"]["tls"] = False
    bench = _make_bench(tmp_path, data)
    bench.create_directories()
    (tmp_path / "sites" / "site1.example.com").mkdir(parents=True)
    (tmp_path / "sites" / "site1.example.com" / "site_config.json").write_text("{}")

    manager = NginxManager(bench)
    # Even when told SSL is ready, a tls=False admin stays HTTP-only.
    manager.generate_config(ssl_ready=True)

    content = (tmp_path / "config" / "nginx" / "sites" / "_admin.conf").read_text()
    assert "server_name admin.example.com;" in content
    assert "listen 80;" in content
    assert "return 301 https://" not in content
    assert "ssl_certificate" not in content


def test_admin_tls_enabled_redirects_http_to_https(tmp_path: Path) -> None:
    # admin.tls = True with a cert on disk: nginx serves HTTPS and redirects the
    # whole HTTP vhost to it.
    data = copy.deepcopy(_ADMIN_SYSTEMD_DATA)
    data["admin"]["tls"] = True
    bench = _make_bench(tmp_path, data)
    bench.create_directories()
    (tmp_path / "sites" / "site1.example.com").mkdir(parents=True)
    (tmp_path / "sites" / "site1.example.com" / "site_config.json").write_text("{}")

    manager = NginxManager(bench)
    manager.admin_cert_exists = lambda: True  # pretend the admin cert is present
    manager.generate_config(ssl_ready=True)

    content = (tmp_path / "config" / "nginx" / "sites" / "_admin.conf").read_text()
    assert "listen 443 ssl http2" in content
    assert "ssl_certificate" in content
    assert "return 301 https://$host$request_uri" in content


def test_admin_domain_proxy_under_supervisor(tmp_path: Path) -> None:
    data = copy.deepcopy(_ADMIN_SYSTEMD_DATA)
    data["production"]["process_manager"] = "supervisor"
    bench = _make_bench(tmp_path, data)
    bench.create_directories()
    (tmp_path / "sites" / "site1.example.com").mkdir(parents=True)
    (tmp_path / "sites" / "site1.example.com" / "site_config.json").write_text("{}")

    manager = NginxManager(bench)
    manager.generate_config(ssl_ready=False)

    admin_conf = tmp_path / "config" / "nginx" / "sites" / "_admin.conf"
    assert admin_conf.exists()
    content = admin_conf.read_text()
    assert "server_name admin.example.com;" in content
    # Supervisor runs the admin directly on admin.port.
    assert f"proxy_pass         http://127.0.0.1:{bench.config.admin.port};" in content


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


def test_no_canonical_redirect_without_explicit_primary(tmp_path: Path) -> None:
    # Without an explicit primary, site.primary falls back to the (internal) site
    # name; a 301 there would strand public traffic on an unreachable host.
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = NginxManager(bench)
    site = SiteConfig(name="site.localhost", apps=["frappe"], domains=["www.example.com"])

    config_text = manager._generate_site_config(site, ssl_ready=False)

    assert "return 301 $scheme://" not in config_text


def test_canonical_redirect_with_explicit_primary(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = NginxManager(bench)
    site = SiteConfig(
        name="site.localhost",
        apps=["frappe"],
        domains=["www.example.com"],
        primary_domain="www.example.com",
    )

    config_text = manager._generate_site_config(site, ssl_ready=False)

    assert 'if ($host != "www.example.com")' in config_text
    assert "return 301 $scheme://www.example.com$request_uri;" in config_text


def test_proxy_headers_present(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = NginxManager(bench)

    config = manager._generate_site_config(_BASE_SITE, ssl_ready=False)

    assert "X-Frappe-Site-Name" in config
    assert "X-Forwarded-Proto" in config


def test_no_proxy_servers_keeps_direct_defaults(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = NginxManager(bench)
    manager._proxy_servers_cache = []  # no provider / direct exposure

    config = manager._generate_site_config(_BASE_SITE, ssl_ready=False)

    assert "set_real_ip_from" not in config
    assert "deny" not in config
    assert "X-Forwarded-For    $proxy_add_x_forwarded_for" in config


def test_proxy_servers_trust_only_those_ips(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = NginxManager(bench)
    manager._proxy_servers_cache = ["203.0.113.5", "203.0.113.6"]

    config = manager._generate_site_config(_BASE_SITE, ssl_ready=False)

    # Trust the proxy IPs and accept connections from them alone.
    assert "set_real_ip_from   203.0.113.5;" in config
    assert "set_real_ip_from   203.0.113.6;" in config
    assert "real_ip_header     X-Forwarded-For;" in config
    assert "allow              203.0.113.5;" in config
    assert "allow              203.0.113.6;" in config
    assert "deny               all;" in config
    # Trust the proxy's X-Forwarded-For unchanged rather than appending to it.
    assert "X-Forwarded-For    $http_x_forwarded_for" in config
    assert "$proxy_add_x_forwarded_for" not in config


def test_two_benches_generate_non_conflicting_configs(tmp_path: Path) -> None:
    """All benches share one nginx, so each bench's include.conf must use a
    uniquely-named upstream and its own admin server_name."""
    def _include_for(name: str, http_port: int, admin_domain: str) -> str:
        data = copy.deepcopy(_BASE_DATA)
        data["bench"] = {"name": name, "python": "3.14", "http_port": http_port}
        data["admin"] = {"domain": admin_domain}
        bench = _make_bench(tmp_path / name, data)
        bench.create_directories()
        NginxManager(bench).generate_config(ssl_ready=False)
        return (tmp_path / name / "config" / "nginx" / "include.conf").read_text()

    a = _include_for("alpha", 8000, "alpha-admin.localhost")
    b = _include_for("beta", 8001, "beta-admin.localhost")

    assert "upstream bench-alpha {" in a
    assert "upstream bench-beta {" in b
    assert "bench-beta" not in a and "bench-alpha" not in b


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


def test_site_config_has_error_pages(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = NginxManager(bench)

    config = manager._generate_site_config(_BASE_SITE, ssl_ready=False)

    assert "error_page 404 /_errors/404.html;" in config
    assert "error_page 502 /_errors/502.html;" in config
    assert "error_page 503 /_errors/503.html;" in config
    assert "location ^~ /_errors/ {" in config
    assert "internal;" in config


def test_generate_config_writes_error_page_files(tmp_path: Path) -> None:
    data = copy.deepcopy(_BASE_DATA)
    data["admin"] = {"domain": "admin.example.com"}
    bench = _make_bench(tmp_path, data)
    bench.create_directories()
    site_dir = bench.sites_path / "site1.example.com"
    site_dir.mkdir()
    (site_dir / "site_config.json").write_text("{}")

    NginxManager(bench).generate_config(ssl_ready=False)

    error_dir = bench.config_path / "nginx" / "error_pages"
    assert sorted(p.name for p in error_dir.iterdir()) == ["403.html", "404.html", "502.html", "503.html"]
    assert "404" in (error_dir / "404.html").read_text()
    # admin vhost also serves the custom pages
    admin_conf = (bench.config_path / "nginx" / "sites" / "_admin.conf").read_text()
    assert "/_errors/" in admin_conf


def test_catchall_default_server(tmp_path: Path) -> None:
    from pathlib import Path as _P

    bench = _make_bench(tmp_path, _BASE_DATA)
    conf = NginxManager(bench)._render_catchall(80, 443, _P("/usr/share/nginx/bench-error-pages"))

    assert "listen 80 default_server;" in conf
    assert "server_name _;" in conf
    assert "error_page 404 /_errors/404.html;" in conf
    assert "return 404;" in conf
    assert "alias /usr/share/nginx/bench-error-pages/;" in conf
    # A 443 default_server is required so https requests for an http-only bench
    # are rejected instead of falling through to the first TLS vhost.
    assert "listen 443 ssl http2 default_server;" in conf
    assert "ssl_reject_handshake on;" in conf


# ── Firewall ────────────────────────────────────────────────────────────────

def _firewall_data(enabled: bool, default: str, rules: list) -> dict:
    data = copy.deepcopy(_BASE_DATA)
    data["firewall"] = {"enabled": enabled, "default": default, "rules": rules}
    return data


def test_firewall_master_switch_off_renders_nothing(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _firewall_data(False, "deny", [{"ip": "1.2.3.4", "action": "deny"}]))
    assert NginxManager(bench)._render_firewall() == ""


def test_firewall_blocklist_emits_only_deny(tmp_path: Path) -> None:
    rules = [{"ip": "203.0.113.4", "action": "deny"}]
    bench = _make_bench(tmp_path, _firewall_data(True, "allow", rules))
    out = NginxManager(bench)._render_firewall()
    assert "deny 203.0.113.4;" in out
    assert "deny all;" not in out   # default allow => no terminal deny


def test_firewall_allowlist_emits_allow_then_deny_all(tmp_path: Path) -> None:
    rules = [{"ip": "203.0.113.4", "action": "allow"}]
    bench = _make_bench(tmp_path, _firewall_data(True, "deny", rules))
    out = NginxManager(bench)._render_firewall()
    assert out.index("allow 203.0.113.4;") < out.index("deny all;")


def test_firewall_appears_in_site_and_admin_blocks(tmp_path: Path) -> None:
    data = _firewall_data(True, "allow", [{"ip": "203.0.113.4", "action": "deny"}])
    data["admin"] = {"domain": "admin.example.com"}
    bench = _make_bench(tmp_path, data)
    manager = NginxManager(bench)
    assert "deny 203.0.113.4;" in manager._generate_site_config(_BASE_SITE, ssl_ready=False)
    assert "deny 203.0.113.4;" in manager._generate_admin_config(ssl_ready=False)


def test_error_pages_include_403_and_errors_allow_all(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = NginxManager(bench)
    block = manager._render_error_pages()
    assert "error_page 403 /_errors/403.html;" in block
    assert "allow all;" in block  # blocked client can still fetch its 403 page


def test_install_config_rolls_back_symlink_when_reload_fails(tmp_path: Path) -> None:
    """A broken config for one bench must not leave a dangling symlink behind —
    that breaks the shared nginx.conf test for every other bench on the box."""
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = NginxManager(bench)
    symlink_path = tmp_path / "test-bench.conf"

    with patch.object(manager, "reload", side_effect=CommandError("nginx -t failed", returncode=1)), \
         patch("pilot.managers.nginx_manager.run_command") as mock_run:
        with pytest.raises(CommandError):
            manager._reload_or_rollback(symlink_path)

    mock_run.assert_called_once()
    assert mock_run.call_args[0][0][-2:] == ["unlink", str(symlink_path)]


def test_prune_dangling_symlinks_removes_only_broken_ones(tmp_path: Path) -> None:
    """A bench dropped without going through its own teardown (e.g. its
    directory deleted directly) leaves its vhost symlink dangling; that alone
    fails nginx -t for every bench sharing the config dir, so install_config
    must sweep it away regardless of which bench it belonged to."""
    nginx_dir = tmp_path / "conf.d"
    nginx_dir.mkdir()
    target = tmp_path / "real-target.conf"
    target.write_text("server {}\n")
    (nginx_dir / "alive-bench.conf").symlink_to(target)
    (nginx_dir / "dropped-bench.conf").symlink_to(tmp_path / "deleted-bench" / "include.conf")
    (nginx_dir / "00-bench-default.conf").write_text("server {}\n")

    with patch("pilot.managers.nginx_manager.run_command") as mock_run:
        NginxManager._prune_dangling_symlinks(nginx_dir)

    mock_run.assert_called_once()
    assert mock_run.call_args[0][0][-2:] == ["unlink", str(nginx_dir / "dropped-bench.conf")]
