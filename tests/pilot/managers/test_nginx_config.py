"""Tests for nginx config generation - no real nginx required.

The renderer turns a bench + per-site TLS readiness into config text; these
tests assert on that text. NginxManager tests cover the TLS decisions and the
on-disk files it writes.
"""

import copy
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest

from pilot.config import BenchConfig, SiteConfig
from pilot.core.bench import Bench
from pilot.exceptions import CommandError
from pilot.managers.nginx import NginxConfigRenderer, NginxManager

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
    """A renderer with the proxy lookup stubbed so tests never hit the provider."""
    renderer = NginxConfigRenderer(_make_bench(tmp_path, data or _BASE_DATA))
    renderer._proxy_servers_cache = proxy_servers or []
    return renderer


def _site_config(tmp_path: Path, site: SiteConfig, ssl: bool = False, **kwargs) -> str:
    return _renderer(tmp_path, **kwargs).generate_bench_config([(site, ssl)], admin_ssl=False)


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


# --- firewall ---------------------------------------------------------------


def _firewall_config(tmp_path: Path, enabled: bool, default: str, rules: list, proxy=None) -> str:
    data = copy.deepcopy(_BASE_DATA)
    data["firewall"] = {"enabled": enabled, "default": default, "rules": rules}
    data["admin"] = {"domain": "admin.example.com"}
    renderer = _renderer(tmp_path, data, proxy_servers=proxy)
    return renderer.generate_bench_config([(_BASE_SITE, False)], admin_ssl=False)


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
        active = _renderer(tmp_path, data).generate_bench_config([(_BASE_SITE, False)], admin_ssl=False)
    with patch.object(nginx.WafManager, "is_installed", staticmethod(lambda: False)):
        inactive = _renderer(tmp_path, data).generate_bench_config([(_BASE_SITE, False)], admin_ssl=False)

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

    assert "proxy_pass         http://127.0.0.1:7000;" in config


def test_admin_ssl_redirects_http_to_https(tmp_path: Path) -> None:
    config = _renderer(tmp_path, _ADMIN_DATA).generate_bench_config([], admin_ssl=True)

    assert "listen 443 ssl http2" in config
    assert "ssl_certificate" in config
    assert "return 301 https://$host$request_uri" in config


def test_no_admin_vhost_without_domain(tmp_path: Path) -> None:
    config = _renderer(tmp_path, _BASE_DATA).generate_bench_config([(_BASE_SITE, False)], admin_ssl=False)
    assert "location = /api/v1/health" not in config


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


def test_every_vhost_gets_error_log_including_admin(tmp_path: Path) -> None:
    # Unlike access_log (app requests only, site vhosts only), error_log covers
    # every vhost - admin included - since operational errors matter there too.
    data = copy.deepcopy(_BASE_DATA)
    data["admin"] = {"domain": "admin.example.com"}
    config = _renderer(tmp_path, data).generate_bench_config([(_BASE_SITE, False)], admin_ssl=False)

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
    manager.has_covering_cert = lambda site: True
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
    """All benches share one nginx, so each bench's config must use a uniquely
    named upstream."""

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

    with (
        patch.object(manager, "reload", side_effect=CommandError("nginx -t failed", returncode=1)),
        patch("pilot.managers.nginx.run_command") as mock_run,
        pytest.raises(CommandError),
    ):
        manager._reload_or_rollback(symlink_path)

    mock_run.assert_called_once()
    assert mock_run.call_args[0][0][-2:] == ["unlink", str(symlink_path)]


def test_write_nginx_logrotate_content(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = NginxManager(bench)

    with patch.object(manager, "_stage_and_copy") as mock_stage:
        manager._write_nginx_logrotate()

    mock_stage.assert_called_once()
    content, target = mock_stage.call_args[0]
    assert target == Path("/etc/logrotate.d/test-bench-nginx")
    assert str(bench.logs_path / "nginx-access.log") in content
    assert str(bench.logs_path / "nginx-error.log") in content
    assert "copytruncate" in content
    assert "rotate 3" in content


def test_stage_and_copy_creates_missing_nginx_config_dir(tmp_path: Path) -> None:
    """install() runs setup_sudoers() before generate_config() ever mkdirs
    config/nginx - staging must not assume that directory already exists."""
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


def test_setup_sudoers_grants_only_start_stop_reload(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = NginxManager(bench)
    sudoers_file = Path("/etc/sudoers.d/runner-pilot-nginx")

    with (
        patch("pwd.getpwuid") as mock_getpwuid,
        patch("pilot.managers.sudoers.stage_and_copy") as mock_stage,
        patch("pilot.managers.sudoers.run_command") as mock_run,
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
    assert content.rstrip().endswith("reload nginx")
    assert "ALL=(ALL) NOPASSWD: ALL" not in content

    mock_run.assert_called_once()
    assert mock_run.call_args.args[0][-3:] == ["chmod", "440", str(sudoers_file)]


def test_install_config_writes_nginx_logrotate(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path, _BASE_DATA)
    manager = NginxManager(bench)

    with (
        patch.object(manager, "_write_nginx_logrotate") as mock_logrotate,
        patch.object(manager, "install_default_server"),
        patch.object(manager, "_set_worker_user"),
        patch.object(manager, "_reload_or_rollback"),
        patch.object(manager, "_prune_dangling_symlinks"),
        patch("pilot.managers.nginx.run_command"),
    ):
        manager.install_config()

    mock_logrotate.assert_called_once()


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
