import copy
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest

from pilot.config import BenchConfig, SiteConfig
from pilot.config.central import HostnameAlias
from pilot.core.bench import Bench
from pilot.exceptions import CommandError
from pilot.managers.nginx import (
    CLOUD_ADMIN_PREFIX,
    CLOUD_SITE_PREFIX,
    NginxConfigRenderer,
    NginxManager,
    vm_hostname_pattern,
)

_BASE_DATA: dict = {
    "bench": {"name": "test-bench", "python": "3.14"},
    "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
    "mariadb": {"root_password": "root"},
    "redis": {"cache_port": 13000, "queue_port": 11000},
}

_BASE_SITE = SiteConfig(name="site1.example.com", apps=["frappe"])


def _make_bench(tmp_path: Path, data: dict) -> Bench:
    return Bench(BenchConfig._from_dict(data), tmp_path)


def _renderer(tmp_path: Path, data: dict | None = None, proxy_servers: list[str] | None = None):
    """Build a renderer without contacting the provider."""
    renderer = NginxConfigRenderer(_make_bench(tmp_path, data or _BASE_DATA))
    renderer._proxy_servers_cache = proxy_servers or []
    return renderer


def _site_config(tmp_path: Path, site: SiteConfig, ssl: bool = False, **kwargs) -> str:
    """The legacy `ssl` flag enables TLS for every site domain."""
    tls_domains = site.all_domains if ssl else []
    return _renderer(tmp_path, **kwargs).generate_bench_config([(site, tls_domains)], admin_ssl=False)


# --- site vhost -------------------------------------------------------------


def test_http_only_site_has_no_tls(tmp_path: Path) -> None:
    config = _site_config(tmp_path, _BASE_SITE, ssl=False)

    assert "listen 80;" in config
    assert "listen [::]:80;" in config
    assert "ssl_certificate" not in config
    assert "return 301 https://" not in config


def test_ssl_site_redirects_http_and_serves_https(tmp_path: Path) -> None:
    config = _site_config(tmp_path, _BASE_SITE, ssl=True)

    assert "listen 443 ssl http2;" in config
    assert "listen [::]:443 ssl http2;" in config
    assert "ssl_certificate" in config
    assert "ssl_certificate_key" in config
    assert "return 301 https://$host$request_uri" in config


def test_server_name_lists_all_domains(tmp_path: Path) -> None:
    site = SiteConfig(name="site1.example.com", apps=["frappe"], domains=["www.site1.example.com"])
    config = _site_config(tmp_path, site)

    assert "server_name site1.example.com www.site1.example.com;" in config


def test_no_canonical_redirect_without_explicit_primary(tmp_path: Path) -> None:
    # Without an explicit primary, site.primary falls back to the (internal) site
    # name; a 301 there would strand public traffic on an unreachable host.
    site = SiteConfig(name="site.localhost", apps=["frappe"], domains=["www.example.com"])

    assert "return 301 $scheme://" not in _site_config(tmp_path, site)


def test_canonical_redirect_with_explicit_primary(tmp_path: Path) -> None:
    site = SiteConfig(
        name="site.localhost",
        apps=["frappe"],
        domains=["www.example.com"],
        primary_domain="www.example.com",
    )
    config = _site_config(tmp_path, site)

    assert 'if ($host != "www.example.com")' in config
    assert "return 301 $scheme://www.example.com$request_uri;" in config


def test_canonical_redirect_preserves_edge_proxy_scheme(tmp_path: Path) -> None:
    site = SiteConfig(
        name="site.localhost",
        apps=["frappe"],
        domains=["www.example.com"],
        primary_domain="www.example.com",
    )
    config = _site_config(tmp_path, site, proxy_servers=["203.0.113.5"])

    assert "return 301 $http_x_forwarded_proto://www.example.com$request_uri;" in config


def test_proxy_headers_and_error_pages_present(tmp_path: Path) -> None:
    config = _site_config(tmp_path, _BASE_SITE)

    assert "X-Frappe-Site-Name" in config
    assert "X-Forwarded-Proto" in config
    assert "error_page 404 /_errors/404.html;" in config
    assert "location ^~ /_errors/ {" in config


def test_socketio_proxies_to_socketio_port(tmp_path: Path) -> None:
    data = copy.deepcopy(_BASE_DATA)
    data["bench"]["socketio_port"] = 9000
    config = _site_config(tmp_path, _BASE_SITE, data=data)

    assert "location /socket.io {" in config
    assert "proxy_pass         http://127.0.0.1:9000;" in config
    assert "proxy_set_header   Upgrade $http_upgrade;" in config


def test_dual_stack_listeners(tmp_path: Path) -> None:
    config = _site_config(tmp_path, _BASE_SITE, ssl=True)

    for line in ("listen 80;", "listen [::]:80;", "listen 443 ssl http2;", "listen [::]:443 ssl http2;"):
        assert line in config


# --- public files -----------------------------------------------------------


def test_public_files_are_served_regardless_of_extension(tmp_path: Path) -> None:
    """An extension allowlist used to drop anything but images and documents,
    so kernels, disk images and archives 404ed even though they were on disk."""
    config = _site_config(tmp_path, _BASE_SITE)

    assert "location /files/ {" in config
    assert f"root {tmp_path}/sites/site1.example.com/public;" in config
    # No extension list guards the prefix location.
    assert "jpg|jpeg|png" not in config


def test_public_files_fall_back_to_the_app(tmp_path: Path) -> None:
    config = _site_config(tmp_path, _BASE_SITE)

    assert "try_files $uri @app;" in config
    assert "location @app {" in config
    assert "proxy_pass         http://bench-test-bench;" in config
    assert "proxy_set_header   X-Frappe-Site-Name site1.example.com;" in config


def test_markup_uploads_are_forced_to_download(tmp_path: Path) -> None:
    """nginx serves public files off disk, so it must repeat the attachment
    header frappe would have sent. Inline user markup is stored XSS."""
    config = _site_config(tmp_path, _BASE_SITE)
    matcher = next(line for line in config.splitlines() if "location ~* ^/files/" in line)

    assert 'add_header Content-Disposition "attachment";' in config
    for extension in ("svg", "svgz", "html", "xhtml", "xml", "swf"):
        assert f"{extension}|" in matcher or f"{extension})" in matcher


def test_force_download_match_is_case_insensitive(tmp_path: Path) -> None:
    """A case-sensitive match would let evil.SVG render inline."""
    config = _site_config(tmp_path, _BASE_SITE)

    assert "location ~* ^/files/" in config


def test_force_download_location_precedes_the_prefix_location(tmp_path: Path) -> None:
    """nginx prefers a regex location over a prefix one, but only the first
    regex that matches, so this block has to come before any other /files regex."""
    config = _site_config(tmp_path, _BASE_SITE)

    assert config.index("location ~* ^/files/") < config.index("location /files/ {")


def test_every_files_fallback_has_a_named_location(tmp_path: Path) -> None:
    """try_files pointing at an undeclared @app fails nginx at startup, which a
    template test is the only cheap place to catch."""
    config = _site_config(tmp_path, _BASE_SITE, ssl=True)

    for block in config.split("server {")[1:]:
        if "try_files $uri @app;" in block:
            assert "location @app {" in block


# --- trusted proxy ----------------------------------------------------------


def test_direct_exposure_keeps_default_xff(tmp_path: Path) -> None:
    config = _site_config(tmp_path, _BASE_SITE, proxy_servers=[])

    assert "set_real_ip_from" not in config
    assert "realip_remote_addr" not in config
    assert "X-Forwarded-For    $proxy_add_x_forwarded_for" in config


def test_trusted_proxies_gate_peer_and_trust_xff(tmp_path: Path) -> None:
    config = _site_config(tmp_path, _BASE_SITE, proxy_servers=["203.0.113.5", "203.0.113.6"])

    assert "set_real_ip_from   203.0.113.5;" in config
    assert "set_real_ip_from   203.0.113.6;" in config
    assert "real_ip_header     X-Forwarded-For;" in config
    assert (
        r'if ($realip_remote_addr ~ "^(203\.0\.113\.5|203\.0\.113\.6)$") { set $bench_from_proxy 1; }'
        in config
    )
    assert "if ($bench_from_proxy = 0) { return 403; }" in config
    assert r'if ($request_uri ~ "^/\.well-known/acme-challenge/") { set $bench_from_proxy 1; }' in config
    assert "X-Forwarded-For    $http_x_forwarded_for" in config
    assert "$proxy_add_x_forwarded_for" not in config
    assert "X-Forwarded-Proto  $http_x_forwarded_proto" in config
    assert "set $pilot_socketio_origin $http_origin;" in config
    assert 'if ($pilot_socketio_origin = "") {' in config
    assert "set $pilot_socketio_origin $http_x_forwarded_proto://$http_host;" in config
    assert "proxy_set_header   Origin $pilot_socketio_origin;" in config


def test_direct_site_builds_socketio_origin_from_local_scheme(tmp_path: Path) -> None:
    config = _site_config(tmp_path, _BASE_SITE, proxy_servers=[])

    assert "X-Forwarded-Proto  $scheme" in config
    assert "set $pilot_socketio_origin $http_origin;" in config
    assert "set $pilot_socketio_origin $scheme://$http_host;" in config
    assert "proxy_set_header   Origin $pilot_socketio_origin;" in config


# --- firewall ---------------------------------------------------------------


def _firewall_config(tmp_path: Path, enabled: bool, default: str, rules: list, proxy=None) -> str:
    data = copy.deepcopy(_BASE_DATA)
    data["firewall"] = {"enabled": enabled, "default": default, "rules": rules}
    data["admin"] = {"domain": "admin.example.com"}
    renderer = _renderer(tmp_path, data, proxy_servers=proxy)
    return renderer.generate_bench_config([(_BASE_SITE, [])], admin_ssl=False)


def test_firewall_off_renders_nothing(tmp_path: Path) -> None:
    out = _firewall_config(tmp_path, False, "deny", [{"ip": "1.2.3.4", "action": "deny"}])
    assert "deny 1.2.3.4;" not in out
    assert "deny all;" not in out


def test_firewall_blocklist_emits_only_deny(tmp_path: Path) -> None:
    out = _firewall_config(tmp_path, True, "allow", [{"ip": "203.0.113.4", "action": "deny"}])
    assert "deny 203.0.113.4;" in out
    assert "deny all;" not in out  # default allow => no terminal deny


def test_firewall_allowlist_emits_allow_then_deny_all(tmp_path: Path) -> None:
    out = _firewall_config(tmp_path, True, "deny", [{"ip": "203.0.113.4", "action": "allow"}])
    assert out.index("allow 203.0.113.4;") < out.index("deny all;")


def test_firewall_never_blocks_trusted_proxy(tmp_path: Path) -> None:
    # allow wins as access rules are first-match, even with an explicit deny.
    out = _firewall_config(
        tmp_path, True, "deny", [{"ip": "203.0.113.5", "action": "deny"}], proxy=["203.0.113.5"]
    )
    assert out.index("allow 203.0.113.5;") < out.index("deny 203.0.113.5;")
    assert out.index("allow 203.0.113.5;") < out.index("deny all;")


def test_firewall_applies_to_site_and_admin(tmp_path: Path) -> None:
    out = _firewall_config(tmp_path, True, "allow", [{"ip": "203.0.113.4", "action": "deny"}])
    # both the site and admin server blocks carry the rule
    assert out.count("deny 203.0.113.4;") == 2


# --- WAF --------------------------------------------------------------------


def test_waf_directives_gate_on_install(tmp_path: Path) -> None:
    from pilot.managers import nginx

    data = copy.deepcopy(_BASE_DATA)
    data["waf"] = {"enabled": True}
    data["admin"] = {"domain": "admin.example.com"}

    with patch.object(nginx.WafManager, "is_installed", staticmethod(lambda: True)):
        active = _renderer(tmp_path, data).generate_bench_config([(_BASE_SITE, [])], admin_ssl=False)
    with patch.object(nginx.WafManager, "is_installed", staticmethod(lambda: False)):
        inactive = _renderer(tmp_path, data).generate_bench_config([(_BASE_SITE, [])], admin_ssl=False)

    assert active.count("modsecurity on;") == 2  # site + admin
    assert "modsecurity" not in inactive


# --- admin vhost ------------------------------------------------------------

_ADMIN_DATA: dict = {
    **_BASE_DATA,
    "production": {"process_manager": "systemd", "nginx": True},
    "admin": {"enabled": True, "port": 7000, "password": "x", "domain": "admin.example.com"},
}


def test_admin_proxy_port_under_systemd(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _ADMIN_DATA)
    config = _renderer(tmp_path, _ADMIN_DATA).generate_bench_config([], admin_ssl=False)

    assert "server_name admin.example.com;" in config
    # systemd socket-activates the admin on its internal port.
    assert f"proxy_pass         http://127.0.0.1:{bench.config.admin.internal_port};" in config


def test_admin_proxy_port_under_supervisor(tmp_path: Path) -> None:
    data = copy.deepcopy(_ADMIN_DATA)
    data["production"]["process_manager"] = "supervisor"
    config = _renderer(tmp_path, data).generate_bench_config([], admin_ssl=False)

    assert "proxy_pass         http://127.0.0.1:7001;" in config


def test_admin_ssl_redirects_http_to_https(tmp_path: Path) -> None:
    config = _renderer(tmp_path, _ADMIN_DATA).generate_bench_config([], admin_ssl=True)

    assert "listen 443 ssl http2" in config
    assert "ssl_certificate" in config
    assert "return 301 https://$host$request_uri" in config


def test_no_admin_vhost_without_domain(tmp_path: Path) -> None:
    config = _renderer(tmp_path, _BASE_DATA).generate_bench_config([(_BASE_SITE, [])], admin_ssl=False)
    assert "location = /api/v1/health" not in config


def test_setup_finish_gets_open_cors(tmp_path: Path) -> None:
    config = _renderer(tmp_path, _ADMIN_DATA).generate_bench_config([], admin_ssl=False)

    assert "location = /api/v1/setup/actions/finish" in config
    assert "if ($request_method = OPTIONS)" in config
    assert 'Access-Control-Allow-Methods "GET, POST, OPTIONS" always' in config


def test_http_to_https_redirect_still_serves_cors_paths_directly(tmp_path: Path) -> None:
    config = _renderer(tmp_path, _ADMIN_DATA).generate_bench_config([], admin_ssl=True)

    # One location block in the :80 redirect server, one in the :443 admin server.
    assert config.count("location = /api/v1/setup/actions/finish") == 2


# --- access log --------------------------------------------------------------


def test_site_vhost_logs_real_ip_via_pilot_access_format(tmp_path: Path) -> None:
    config = _site_config(tmp_path, _BASE_SITE)

    assert "access_log" in config
    assert "pilot_access" in config
    assert "nginx-access.log" in config


def test_only_the_app_location_gets_request_logging(tmp_path: Path) -> None:
    config = _site_config(tmp_path, _BASE_SITE)

    # Exactly one access_log directive: the proxied app location, not
    # /assets, /files, or /socket.io - those aren't measured by monitor.json.log
    # either, so keep the two IP sources comparable.
    assert config.count("access_log") == 1
    assets_block = config[config.index("location /assets") : config.index("location /socket.io")]
    assert "access_log" not in assets_block
    socketio_block = config[config.index("location /socket.io") :]
    socketio_block = socketio_block[: socketio_block.index("location /")]
    assert "access_log" not in socketio_block


def test_admin_only_vhost_has_no_access_log(tmp_path: Path) -> None:
    data = copy.deepcopy(_BASE_DATA)
    data["admin"] = {"domain": "admin.example.com"}
    config = _renderer(tmp_path, data).generate_bench_config([], admin_ssl=False)

    assert "access_log" not in config


def test_admin_vhost_offloads_editor_dashboard_and_in_app_embed_assets(tmp_path: Path) -> None:
    # Static bundles served straight from disk by nginx, off the single admin worker.
    data = copy.deepcopy(_BASE_DATA)
    data["admin"] = {"domain": "admin.example.com"}
    config = _renderer(tmp_path, data).generate_bench_config([], admin_ssl=False)

    assert "location /editor-assets/" in config
    assert "location /embed/cloud-settings/" in config
    assert "location /assets/" in config
    assert "/static/editor/;" in config
    assert "/static/in-app-embed/cloud-settings/;" in config
    assert "/static/dashboard/assets/;" in config
    assert 'add_header Cache-Control "public, immutable";' in config
    # Assets + API JSON compressed on the wire (nginx's default gzip_types is html-only).
    assert "gzip on;" in config
    assert "application/javascript text/javascript text/css application/json" in config


def test_in_app_embed_bundle_revalidates_instead_of_immutable(tmp_path: Path) -> None:
    # Fixed filename (cloud-settings.js): nginx must let it revalidate so a rebuild
    # is picked up, not pin the old bytes for a year like the hashed dashboard assets.
    data = copy.deepcopy(_BASE_DATA)
    data["admin"] = {"domain": "admin.example.com"}
    config = _renderer(tmp_path, data).generate_bench_config([], admin_ssl=False)

    start = config.index("location /embed/cloud-settings/")
    embed_block = config[start : config.index("}", start)]
    assert 'add_header Cache-Control "no-cache";' in embed_block
    assert 'Cache-Control "public, immutable"' not in embed_block


def test_every_vhost_gets_error_log_including_admin(tmp_path: Path) -> None:
    # Unlike access_log (app requests only, site vhosts only), error_log covers
    # every vhost - admin included - since operational errors matter there too.
    data = copy.deepcopy(_BASE_DATA)
    data["admin"] = {"domain": "admin.example.com"}
    config = _renderer(tmp_path, data).generate_bench_config([(_BASE_SITE, [])], admin_ssl=False)

    assert config.count("error_log") == 2  # one site vhost + one admin vhost
    assert "nginx-error.log" in config


# --- server-wide catch-all --------------------------------------------------


def test_server_config_is_default_server(tmp_path: Path) -> None:
    conf = _renderer(tmp_path).generate_server_config(Path("/usr/share/nginx/bench-error-pages"))

    assert "listen 80 default_server;" in conf
    assert "server_name _;" in conf
    assert "error_page 404 /_errors/404.html;" in conf
    assert "return 404;" in conf
    assert "alias /usr/share/nginx/bench-error-pages/;" in conf
    # A 443 default_server rejects https for http-only benches instead of
    # serving the first TLS vhost's cert.
    assert "listen 443 ssl http2 default_server;" in conf
    assert "ssl_reject_handshake on;" in conf


def test_server_config_declares_pilot_access_log_format(tmp_path: Path) -> None:
    conf = _renderer(tmp_path).generate_server_config(Path("/usr/share/nginx/bench-error-pages"))

    assert "log_format pilot_access" in conf
    assert "$remote_addr" in conf


# --- NginxManager: files and TLS decisions ----------------------------------


def _bench_with_site(tmp_path: Path, data: dict, site_config: str = "{}") -> Bench:
    bench = _make_bench(tmp_path, data)
    bench.create_directories()
    site_dir = tmp_path / "sites" / "site1.example.com"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(site_config)
    return bench


def test_generate_config_writes_full_bench_file(tmp_path: Path) -> None:
    bench = _bench_with_site(tmp_path, _BASE_DATA)
    NginxManager(bench).generate_config(ssl_ready=False)

    include_conf = tmp_path / "config" / "nginx" / "include.conf"
    content = include_conf.read_text()
    assert "upstream bench-test-bench {" in content
    assert "server_name site1.example.com;" in content


def test_generate_config_writes_error_page_files(tmp_path: Path) -> None:
    data = copy.deepcopy(_BASE_DATA)
    data["admin"] = {"domain": "admin.example.com"}
    bench = _bench_with_site(tmp_path, data)

    NginxManager(bench).generate_config(ssl_ready=False)

    error_dir = bench.config_path / "nginx" / "error_pages"
    assert sorted(p.name for p in error_dir.iterdir()) == ["403.html", "404.html", "502.html", "503.html"]
    assert "404" in (error_dir / "404.html").read_text()


def test_localhost_ssl_site_gets_https_when_cert_present(tmp_path: Path) -> None:
    # A pure-.localhost SSL site has no public domains to validate a SAN against,
    # so cert existence alone enables HTTPS (the e2e suite runs on site1.localhost).
    data = copy.deepcopy(_BASE_DATA)
    data["letsencrypt"] = {"email": "admin@example.com"}
    data["admin"] = {"domain": "admin.example.com", "tls": True}
    bench = _make_bench(tmp_path, data)
    bench.create_directories()
    (tmp_path / "sites" / "site1.localhost").mkdir(parents=True)
    (tmp_path / "sites" / "site1.localhost" / "site_config.json").write_text('{"ssl": true}')

    manager = NginxManager(bench)
    manager.has_cert = lambda site: True
    manager.generate_config(ssl_ready=True)

    content = (tmp_path / "config" / "nginx" / "include.conf").read_text()
    assert "listen 443 ssl http2" in content
    assert "return 301 https://$host$request_uri;" in content


def test_admin_tls_disabled_serves_everything_http(tmp_path: Path) -> None:
    # admin.tls = False is bench-wide: even an SSL site with a cert on disk is
    # served plain-HTTP, because a central proxy terminates TLS upstream.
    data = copy.deepcopy(_BASE_DATA)
    data["letsencrypt"] = {"email": "admin@example.com"}
    data["admin"] = {"domain": "admin.example.com", "tls": False}
    bench = _bench_with_site(tmp_path, data, site_config='{"ssl": true}')

    manager = NginxManager(bench)
    manager.has_cert = lambda site: True
    manager.generate_config(ssl_ready=True)

    content = (tmp_path / "config" / "nginx" / "include.conf").read_text()
    assert "listen 80;" in content
    assert "ssl_certificate" not in content
    assert "return 301 https://" not in content


def test_site_without_ssl_flag_stays_http_even_with_cert(tmp_path: Path) -> None:
    # site_config.json lacks "ssl": true, so a cert on disk must not flip the
    # site to HTTPS - the flag is the operator's opt-in, checked bench-wide.
    data = copy.deepcopy(_BASE_DATA)
    data["admin"] = {"domain": "admin.example.com", "tls": True}
    bench = _bench_with_site(tmp_path, data)

    manager = NginxManager(bench)
    manager.has_cert = lambda site: True
    manager.generate_config(ssl_ready=True)

    content = (tmp_path / "config" / "nginx" / "include.conf").read_text()
    assert "listen 80;" in content
    assert "ssl_certificate" not in content
    assert "return 301 https://" not in content


def test_admin_tls_enabled_redirects_admin_to_https(tmp_path: Path) -> None:
    data = copy.deepcopy(_ADMIN_DATA)
    data["admin"]["tls"] = True
    bench = _bench_with_site(tmp_path, data)

    manager = NginxManager(bench)
    with patch.object(NginxManager, "has_admin_cert", new_callable=PropertyMock, return_value=True):
        manager.generate_config(ssl_ready=True)

    content = (tmp_path / "config" / "nginx" / "include.conf").read_text()
    assert "server_name admin.example.com;" in content
    assert "listen 443 ssl http2" in content
    assert "return 301 https://$host$request_uri" in content


def test_two_benches_use_distinct_upstreams(tmp_path: Path) -> None:
    """Each bench gets a uniquely named upstream."""

    def _config_for(name: str, http_port: int) -> str:
        data = copy.deepcopy(_BASE_DATA)
        data["bench"] = {"name": name, "python": "3.14", "http_port": http_port}
        bench = _bench_with_site(tmp_path / name, data)
        NginxManager(bench).generate_config(ssl_ready=False)
        return (tmp_path / name / "config" / "nginx" / "include.conf").read_text()

    a = _config_for("alpha", 8000)
    b = _config_for("beta", 8001)

    assert "upstream bench-alpha {" in a and "server 127.0.0.1:8000;" in a
    assert "upstream bench-beta {" in b and "server 127.0.0.1:8001;" in b
    assert "bench-beta" not in a and "bench-alpha" not in b


def test_install_config_rolls_back_symlink_when_reload_fails(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = NginxManager(bench)
    symlink_path = tmp_path / "test-bench.conf"
    symlink_path.symlink_to(tmp_path / "include.conf")

    with (
        patch.object(manager, "reload", side_effect=CommandError("nginx -t failed", returncode=1)),
        patch("pilot.managers.nginx.run_command") as mock_run,
        pytest.raises(CommandError),
    ):
        manager._reload_or_rollback(symlink_path)

    mock_run.assert_called_once()
    assert mock_run.call_args[0][0][-2:] == ["unlink", str(symlink_path)]


def test_stage_and_copy_creates_missing_nginx_config_dir(tmp_path: Path) -> None:
    """install() sets up sudo before creating config/nginx."""
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = NginxManager(bench)
    nginx_dir = bench.config_path / "nginx"
    assert not nginx_dir.exists()

    with patch("pilot.managers.sudoers.run_command") as mock_run:
        manager._stage_and_copy("content", Path("/etc/logrotate.d/test-bench-nginx"))

    mock_run.assert_called_once()
    assert nginx_dir.is_dir()


def test_stage_and_copy_validates_staged_file_before_copying(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = NginxManager(bench)
    target = Path("/etc/sudoers.d/test-bench-pilot-nginx")

    with patch("pilot.managers.sudoers.run_command") as mock_run:
        manager._stage_and_copy("content", target, validate=["visudo", "-cf"])

    assert mock_run.call_count == 2
    validate_call, cp_call = (call.args[0] for call in mock_run.call_args_list)
    staged = bench.config_path / "nginx" / target.name
    assert validate_call[-3:] == ["visudo", "-cf", str(staged)]
    assert cp_call[-3:] == ["cp", str(staged), str(target)]


def test_production_enables_nginx_at_boot(tmp_path: Path) -> None:
    """The distro package is disabled at install time, so a reboot needs this."""
    manager = NginxManager(_make_bench(tmp_path, _BASE_DATA))

    with (
        patch("pilot.managers.nginx.is_linux", return_value=True),
        patch("pilot.managers.nginx.run_command") as mock_run,
    ):
        manager.enable_at_boot()

    assert mock_run.call_args.args[0][-3:] == ["systemctl", "enable", "nginx"]


def test_an_older_sudo_grant_does_not_end_the_deploy(tmp_path: Path) -> None:
    """A host installed before the grant carried the enable verb keeps deploying."""
    manager = NginxManager(_make_bench(tmp_path, _BASE_DATA))

    with (
        patch("pilot.managers.nginx.is_linux", return_value=True),
        patch("pilot.managers.nginx.run_command", side_effect=CommandError("a password is required")),
    ):
        manager.enable_at_boot()


def test_setup_sudoers_grants_only_the_needed_nginx_verbs(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = NginxManager(bench)
    sudoers_file = Path("/etc/sudoers.d/runner-pilot-nginx")

    with (
        patch("pwd.getpwuid") as mock_getpwuid,
        patch("pilot.managers.sudoers.stage_and_copy") as mock_stage,
        patch("pilot.managers.sudoers.run_command") as mock_run,
        patch.object(NginxManager, "has_passwordless_sudo", False),
    ):
        mock_getpwuid.return_value.pw_name = "runner"
        manager.setup_sudoers()

    content, target = mock_stage.call_args.args[1:3]
    assert mock_stage.call_args.kwargs == {"validate": ["visudo", "-cf"]}
    assert target == sudoers_file
    assert "runner ALL=(ALL) NOPASSWD:" in content
    assert "-t," in content
    assert "-T," in content
    assert "start nginx," in content
    assert "stop nginx," in content
    assert "reload nginx," in content
    assert content.rstrip().endswith("enable nginx")
    assert "ALL=(ALL) NOPASSWD: ALL" not in content

    mock_run.assert_called_once()
    assert mock_run.call_args.args[0][-3:] == ["chmod", "440", str(sudoers_file)]


def test_prune_dangling_symlinks_removes_only_broken_ones(tmp_path: Path) -> None:
    nginx_dir = tmp_path / "conf.d"
    nginx_dir.mkdir()
    target = tmp_path / "real-target.conf"
    target.write_text("server {}\n")
    (nginx_dir / "alive-bench.conf").symlink_to(target)
    (nginx_dir / "dropped-bench.conf").symlink_to(tmp_path / "deleted-bench" / "include.conf")
    (nginx_dir / "00-bench-default.conf").write_text("server {}\n")

    with patch("pilot.managers.nginx.run_command") as mock_run:
        NginxManager._prune_dangling_symlinks(nginx_dir)

    mock_run.assert_called_once()
    assert mock_run.call_args[0][0][-2:] == ["unlink", str(nginx_dir / "dropped-bench.conf")]


def test_config_dir_falls_back_to_platform_default(tmp_path: Path) -> None:
    manager = NginxManager(_make_bench(tmp_path, _BASE_DATA))
    with patch("pilot.managers.nginx.default_nginx_config_dir", return_value=Path("/etc/nginx/conf.d")):
        assert manager.config_dir == Path("/etc/nginx/conf.d")


def test_config_dir_honors_explicit_value(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    bench.config.nginx.config_dir = Path("/custom/nginx/dir")
    assert NginxManager(bench).config_dir == Path("/custom/nginx/dir")


def test_cert_files_exist_falls_back_to_http_on_failure() -> None:
    """Any failure - cert absent, or sudo denied - renders the vhost HTTP-only."""
    from pilot.managers.nginx import cert_files_exist

    denied = CommandError("Command 'sudo' failed with exit code 1.\nsudo: a password is required")
    with patch("pilot.managers.nginx.run_command", side_effect=denied):
        assert cert_files_exist("site.example.com") is False


def test_cert_files_exist_true_when_both_files_present() -> None:
    from pilot.managers.nginx import cert_files_exist

    with patch("pilot.managers.nginx.run_command") as mock_run:
        assert cert_files_exist("site.example.com") is True
    argv = mock_run.call_args.args[0]
    assert argv[-4:] == [
        "/etc/letsencrypt/live/site.example.com/fullchain.pem",
        "-a",
        "-f",
        "/etc/letsencrypt/live/site.example.com/privkey.pem",
    ]


# --- cloud VM hostnames ------------------------------------------------------

_VM_DOMAIN = "par-1.frappe.cloud"
_SITE_GLOB = f"{CLOUD_SITE_PREFIX}*.{_VM_DOMAIN}"
_ADMIN_GLOB = f"{CLOUD_ADMIN_PREFIX}*.{_VM_DOMAIN}"
_SITE_PATTERN = vm_hostname_pattern(_SITE_GLOB)
_ADMIN_PATTERN = vm_hostname_pattern(_ADMIN_GLOB)
_PLACEHOLDER_SITE = SiteConfig(name=f"site-a1b2c3.{_VM_DOMAIN}", apps=["frappe"])


def _cloud_config(
    tmp_path: Path,
    sites: list[tuple[SiteConfig, bool]],
    central_enabled: bool = True,
    hostname_mappings: dict[str, str] | None = None,
    redirect: bool = True,
) -> str:
    data = copy.deepcopy(_BASE_DATA)
    data["admin"] = {"domain": "admin.example.com"}
    renderer = _renderer(tmp_path, data)
    # Central settings are host-shared.
    central = renderer.bench.config.central
    central.enabled = central_enabled
    central.hostname_aliases = [
        HostnameAlias(
            type="admin" if pattern.startswith(CLOUD_ADMIN_PREFIX) else "site",
            pattern=pattern,
            target=target,
            redirect=redirect,
        )
        for pattern, target in (hostname_mappings or {}).items()
    ]
    # Each site is given as (config, serves_https), which here means every one of
    # its domains terminates TLS on this host.
    entries = [(site, site.all_domains if ssl else []) for site, ssl in sites]
    return renderer.generate_bench_config(entries, admin_ssl=False)


def test_vm_hostnames_serve_site_and_admin(tmp_path: Path) -> None:
    config = _cloud_config(
        tmp_path,
        [(_PLACEHOLDER_SITE, False)],
        hostname_mappings={_SITE_GLOB: _PLACEHOLDER_SITE.name, _ADMIN_GLOB: "admin.example.com"},
    )

    assert _SITE_PATTERN in config
    assert _ADMIN_PATTERN in config
    assert "return 301 http://site-a1b2c3.par-1.frappe.cloud$request_uri;" in config
    assert "return 301 http://admin.example.com$request_uri;" in config


def test_vm_hostname_redirects_to_renamed_site(tmp_path: Path) -> None:
    renamed = SiteConfig(name="shop.example.com", apps=["frappe"])
    config = _cloud_config(tmp_path, [(renamed, False)], hostname_mappings={_SITE_GLOB: renamed.name})

    assert _SITE_PATTERN in config
    assert "return 301 http://shop.example.com$request_uri;" in config


def test_vm_hostname_redirects_to_https_site(tmp_path: Path) -> None:
    renamed = SiteConfig(name="shop.example.com", apps=["frappe"], ssl=True)
    config = _cloud_config(tmp_path, [(renamed, True)], hostname_mappings={_SITE_GLOB: renamed.name})

    assert "return 301 https://shop.example.com$request_uri;" in config


def test_vm_hostname_targets_named_site(tmp_path: Path) -> None:
    second = SiteConfig(name="other.example.com", apps=["frappe"])
    config = _cloud_config(
        tmp_path,
        [(_PLACEHOLDER_SITE, False), (second, False)],
        hostname_mappings={_SITE_GLOB: _PLACEHOLDER_SITE.name},
    )

    assert _SITE_PATTERN in config
    assert f"X-Frappe-Site-Name site-a1b2c3.{_VM_DOMAIN}" in config


def test_vm_hostname_pattern_targets_site(tmp_path: Path) -> None:
    config = _cloud_config(
        tmp_path,
        [(_PLACEHOLDER_SITE, False)],
        hostname_mappings={_SITE_GLOB: _PLACEHOLDER_SITE.name},
    )

    assert f"X-Frappe-Site-Name site-a1b2c3.{_VM_DOMAIN}" in config


def test_vm_alias_is_not_a_site_domain(tmp_path: Path) -> None:
    config = _cloud_config(
        tmp_path, [(_PLACEHOLDER_SITE, False)], hostname_mappings={_SITE_GLOB: _PLACEHOLDER_SITE.name}
    )

    assert f"server_name {_SITE_PATTERN};" in config
    assert f"server_name site-a1b2c3.localhost {_SITE_PATTERN}" not in config


def test_aliases_are_http_only(tmp_path: Path) -> None:
    site = SiteConfig(name="shop.example.com", apps=["frappe"], ssl=True)
    config = _cloud_config(tmp_path, [(site, True)], hostname_mappings={_SITE_GLOB: site.name})

    alias_block = config.split(_SITE_PATTERN)[1].split("}\n\nserver")[0]
    assert "listen 443" not in alias_block
    assert "return 301 https://shop.example.com$request_uri;" in alias_block


def _alias_block(config: str) -> str:
    """The site alias server block, which ends at the unindented closing brace."""
    return config.split(f"server_name {_SITE_PATTERN};")[1].split("\n}\n")[0]


def test_a_serving_alias_serves_static_files(tmp_path: Path) -> None:
    """An alias that serves the site needs the static locations of a site vhost.

    With only the application upstream, every /assets request reaches gunicorn,
    which does not serve them.
    """
    config = _cloud_config(
        tmp_path,
        [(_PLACEHOLDER_SITE, False)],
        hostname_mappings={_SITE_GLOB: _PLACEHOLDER_SITE.name},
        redirect=False,
    )

    alias_block = _alias_block(config)
    assert f"root {tmp_path}/sites;" in alias_block
    assert "location /assets" in alias_block
    assert "location /socket.io" in alias_block
    assert f"root {tmp_path}/sites/{_PLACEHOLDER_SITE.name}/public;" in alias_block
    assert "return 301" not in alias_block


def test_a_serving_alias_serves_public_files_of_any_extension(tmp_path: Path) -> None:
    config = _cloud_config(
        tmp_path,
        [(_PLACEHOLDER_SITE, False)],
        hostname_mappings={_SITE_GLOB: _PLACEHOLDER_SITE.name},
        redirect=False,
    )

    alias_block = _alias_block(config)
    assert "location /files/ {" in alias_block
    assert "location ~* ^/files/" in alias_block
    assert 'add_header Content-Disposition "attachment";' in alias_block
    assert "location @app {" in alias_block
    assert f"proxy_set_header   X-Frappe-Site-Name {_PLACEHOLDER_SITE.name};" in alias_block


def test_a_redirecting_alias_has_no_static_locations(tmp_path: Path) -> None:
    config = _cloud_config(
        tmp_path,
        [(_PLACEHOLDER_SITE, False)],
        hostname_mappings={_SITE_GLOB: _PLACEHOLDER_SITE.name},
    )

    alias_block = _alias_block(config)
    assert "location /assets" not in alias_block
    assert "return 301" in alias_block


def test_self_hosted_bench_has_no_vm_hostnames(tmp_path: Path) -> None:
    config = _cloud_config(
        tmp_path,
        [(_PLACEHOLDER_SITE, False)],
        central_enabled=False,
        hostname_mappings={_SITE_GLOB: _PLACEHOLDER_SITE.name, _ADMIN_GLOB: "admin.example.com"},
    )

    assert _SITE_PATTERN not in config
    assert _ADMIN_PATTERN not in config


def test_unmapped_hostname_is_ignored(tmp_path: Path) -> None:
    config = _cloud_config(
        tmp_path, [(_PLACEHOLDER_SITE, False)], hostname_mappings={"other-*.example.com": "site1.local"}
    )

    assert _SITE_PATTERN not in config
    assert _ADMIN_PATTERN not in config


# --- per-domain TLS ---------------------------------------------------------


def _mixed_site() -> SiteConfig:
    """Build a site with edge and local TLS domains."""
    return SiteConfig(
        name="site-a1b2c3.zone.example",
        apps=["frappe"],
        domains=[{"domain": "shop.customer.com", "tls": True}],
        ssl=False,
    )


def test_a_domain_may_terminate_tls_while_the_site_does_not(tmp_path: Path) -> None:
    site = _mixed_site()

    assert site.tls_domains == ["shop.customer.com"]
    assert site.plain_domains == ["site-a1b2c3.zone.example"]


def test_edge_terminated_domains_are_served_plain_and_never_redirected(tmp_path: Path) -> None:
    site = _mixed_site()
    config = _renderer(tmp_path).generate_bench_config([(site, site.tls_domains)], admin_ssl=False)

    plain = config.split("server_name site-a1b2c3.zone.example;")[1].split("server {")[0]
    assert "return 301 https://" not in plain
    assert "ssl_certificate" not in plain


def test_a_custom_domain_gets_its_own_https_vhost(tmp_path: Path) -> None:
    site = _mixed_site()
    config = _renderer(tmp_path).generate_bench_config([(site, site.tls_domains)], admin_ssl=False)

    assert "listen 443 ssl http2;" in config
    assert "server_name shop.customer.com;" in config
    # The custom domain still gets the HTTP->HTTPS redirect; the wildcard does not.
    assert "return 301 https://$host$request_uri" in config


def test_proxy_protocol_applies_only_to_the_https_listener(tmp_path: Path) -> None:
    site = _mixed_site()
    renderer = _renderer(tmp_path, proxy_servers=["203.0.113.10"])
    renderer.bench.config.proxy.protocol_v2 = True  # host-shared, from common_config.toml
    config = renderer.generate_bench_config([(site, site.tls_domains)], admin_ssl=False)

    assert "listen 443 ssl http2 proxy_protocol;" in config
    assert "listen [::]:443 ssl http2 proxy_protocol;" in config
    assert "listen 80 proxy_protocol;" not in config
    assert "real_ip_header     proxy_protocol;" in config
    assert "real_ip_header     X-Forwarded-For;" in config


def test_proxy_protocol_uses_local_scheme_for_forwarding_and_socketio(tmp_path: Path) -> None:
    site = _mixed_site()
    renderer = _renderer(tmp_path, proxy_servers=["203.0.113.10"])
    renderer.bench.config.proxy.protocol_v2 = True
    config = renderer.generate_bench_config([(site, site.tls_domains)], admin_ssl=False)

    assert "X-Forwarded-Proto  $scheme" in config
    assert "set $pilot_socketio_origin $scheme://$http_host;" in config
    assert "$http_x_forwarded_proto" not in config


def test_without_proxy_protocol_the_https_listener_is_plain(tmp_path: Path) -> None:
    site = _mixed_site()
    config = _renderer(tmp_path, proxy_servers=["203.0.113.10"]).generate_bench_config(
        [(site, site.tls_domains)], admin_ssl=False
    )

    listens = [line.strip() for line in config.splitlines() if line.strip().startswith("listen ")]
    assert "listen 443 ssl http2;" in listens
    assert not any("proxy_protocol" in line for line in listens)


def test_a_site_with_no_tls_domains_renders_one_vhost(tmp_path: Path) -> None:
    site = SiteConfig(name="site1.example.com", apps=["frappe"], domains=["www.example.com"])
    config = _renderer(tmp_path).generate_bench_config([(site, [])], admin_ssl=False)

    assert config.count("server_name site1.example.com www.example.com;") == 1


def test_a_domain_the_certificate_misses_drops_only_itself(tmp_path: Path) -> None:
    """An incomplete certificate falls back to HTTP per domain."""
    bench = _bench_with_site(tmp_path, copy.deepcopy(_BASE_DATA))
    site = SiteConfig(
        name="site1.example.com",
        apps=["frappe"],
        domains=["covered.example.com", "missing.example.com"],
        ssl=True,
    )
    manager = NginxManager(bench)
    manager.has_cert = lambda config: True

    with patch(
        "pilot.managers.letsencrypt.has_domain_coverage",
        side_effect=lambda cert, domains: "missing.example.com" not in domains,
    ):
        serviceable = manager.serviceable_tls_domains(site, ssl_ready=True)

    assert serviceable == ["site1.example.com", "covered.example.com"]


def test_no_tls_at_all_without_a_certificate(tmp_path: Path) -> None:
    bench = _bench_with_site(tmp_path, copy.deepcopy(_BASE_DATA))
    site = SiteConfig(name="site1.example.com", apps=["frappe"], ssl=True)
    manager = NginxManager(bench)
    manager.has_cert = lambda config: False

    assert manager.serviceable_tls_domains(site, ssl_ready=True) == []


def test_the_admin_alias_redirects_to_http_until_a_certificate_exists(tmp_path: Path) -> None:
    """An admin alias redirects to the scheme actually being served."""
    data = copy.deepcopy(_ADMIN_DATA)
    data["admin"]["tls"] = True
    config = _cloud_config(tmp_path, [], hostname_mappings={_ADMIN_GLOB: "admin.example.com"})

    assert "return 301 http://admin.example.com" in config
    assert "return 301 https://admin.example.com" not in config


def test_the_admin_alias_redirects_to_https_once_it_is_served(tmp_path: Path) -> None:
    data = copy.deepcopy(_ADMIN_DATA)
    data["admin"]["tls"] = True
    renderer = _renderer(tmp_path, data)
    central = renderer.bench.config.central
    central.enabled = True
    central.hostname_aliases = [HostnameAlias(type="admin", pattern=_ADMIN_GLOB, target="admin.example.com")]

    config = renderer.generate_bench_config([], admin_ssl=True)

    assert "return 301 https://admin.example.com" in config


def test_a_pinned_lineage_is_what_the_vhost_references(tmp_path: Path) -> None:
    # A pin is checked against sibling benches, and a bare tmp_path's siblings
    # are every other test's leftovers - one may well claim this name. A private
    # parent keeps this bench's siblings empty.
    bench_root = tmp_path / "benches" / "b1"
    bench_root.mkdir(parents=True)
    site = SiteConfig(name="new.example.com", apps=["frappe"], ssl=True, cert_name="old.example.com")
    config = _renderer(bench_root).generate_bench_config([(site, site.tls_domains)], admin_ssl=False)

    assert "/etc/letsencrypt/live/old.example.com/fullchain.pem" in config
    assert "/etc/letsencrypt/live/new.example.com/" not in config
