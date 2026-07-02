from __future__ import annotations

import pwd
import re
import shutil
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING

from pilot.managers.gunicorn_manager import GunicornManager
from pilot.platform import (
    _privileged,
    default_nginx_config_dir,
    get_package_manager,
    is_alpine,
    is_linux,
    service_command,
    service_enable_command,
    service_running,
)
from pilot.utils import run_command

_NGINX_CONF = Path("/etc/nginx/nginx.conf")
_USER_DIRECTIVE = re.compile(r"^[ \t]*user[ \t]+[^;\n]+;", re.MULTILINE)

_SHARED_ERROR_DIR = Path("/usr/share/nginx/bench-error-pages")


def _catchall_conf() -> Path:
    """Server-wide catch-all so unknown hosts get our 404 instead of nginx's
    stock welcome page. One default_server per port, shared by every bench, so
    it lives in the distro's include dir (conf.d on Debian, http.d on Alpine)."""
    return default_nginx_config_dir() / "00-bench-default.conf"


def _stock_default_sites() -> list[Path]:
    """The distro's own default vhost(s), which also claim default_server on :80
    and would conflict with our catch-all. Debian symlinks sites-enabled/default
    (removed on every Linux, as upstream does); Alpine additionally ships
    http.d/default.conf."""
    sites = [Path("/etc/nginx/sites-enabled/default")]
    if is_alpine():
        sites.append(default_nginx_config_dir() / "default.conf")
    return sites

# Custom pages for nginx-generated errors (downed upstream, missing static
# file). App responses pass through unchanged — proxy_intercept_errors is off.
_ERROR_PAGES = {
    403: ("Access blocked", "Your network doesn’t have access to this server."),
    404: ("Page not found", "The page you’re looking for doesn’t exist."),
    502: ("Temporarily unavailable", "The server isn’t responding right now. Please try again in a moment."),
    503: ("Service unavailable", "The service is temporarily unavailable. Please try again shortly."),
}


# $-placeholders (not .format) so the CSS braces below stay literal.
_ERROR_PAGE_TEMPLATE = Template(
    """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$code — $title</title>
<style>
:root{--bg:#fbfbfc;--fg:#1c2024;--muted:#6b7280;--accent:#d1d5db;--font:system-ui,-apple-system,sans-serif}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{display:flex;align-items:center;justify-content:center;background:var(--bg);color:var(--fg);font-family:var(--font);-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
.box{text-align:center;padding:2.5rem 1.5rem;max-width:30rem}
.code{font-size:clamp(3.5rem,12vw,6rem);font-weight:700;line-height:1;letter-spacing:.05em;margin:0;color:var(--fg)}
.rule{width:2.5rem;height:3px;border-radius:999px;background:var(--accent);margin:1.5rem auto}
.title{font-size:1.125rem;font-weight:600;letter-spacing:-.01em;margin:0 0 .4rem}
.msg{font-size:.95rem;line-height:1.55;color:var(--muted);margin:0}
@media(prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e6e8eb;--muted:#9ba1a8;--accent:#2c2f36}}
</style>
</head>
<body>
<div class="box">
<p class="code">$code</p>
<div class="rule"></div>
<p class="title">$title</p>
<p class="msg">$message</p>
</div>
</body>
</html>
"""
)


def _render_error_html(code: int, title: str, message: str) -> str:
    return _ERROR_PAGE_TEMPLATE.substitute(code=code, title=title, message=message)

if TYPE_CHECKING:
    from pilot.config.site_config import SiteConfig
    from pilot.core.bench import Bench


class NginxManager:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench
        self._proxy_servers_cache: list[str] | None = None

    @property
    def _proxy_servers(self) -> list[str]:
        """Edge-proxy IPs the domain provider (if any) puts in front of this bench.
        Looked up once; [] when no provider is installed, i.e. direct exposure."""
        if self._proxy_servers_cache is None:
            from pilot.core.domain_controller import DomainRouteProvider

            self._proxy_servers_cache = DomainRouteProvider.proxy_servers()
        return self._proxy_servers_cache

    def _render_acme_location(self) -> str:
        """ACME HTTP-01 challenge location. `allow all;` overrides any server-level
        proxy-only deny so certbot's validation reaches the challenge files."""
        webroot = self.bench.config.letsencrypt.webroot_path
        return (
            f"    location /.well-known/acme-challenge/ {{\n"
            f"        allow all;\n"
            f"        root {webroot};\n"
            f"        try_files $uri =404;\n"
            f"    }}\n\n"
        )

    def _render_proxy_trust(self) -> str:
        """When edge proxies front this bench, trust only them: accept connections
        from those IPs alone, and read the real client IP from the X-Forwarded-For
        they set. Empty (no restriction) when the bench is directly exposed."""
        proxies = self._proxy_servers
        if not proxies:
            return ""
        return (
            "".join(f"    set_real_ip_from   {ip};\n" for ip in proxies)
            + "    real_ip_header     X-Forwarded-For;\n"
            + "    real_ip_recursive  on;\n"
            + "".join(f"    allow              {ip};\n" for ip in proxies)
            + "    deny               all;\n\n"
        )

    def _render_firewall(self) -> str:
        """Per-vhost IP allow/block list (ngx_http_access_module, first-match-wins).

        Active rules are emitted in order, then a terminal ``deny all;`` only when
        the default policy is deny (allowlist mode); default allow needs no
        terminal line. Empty when the firewall is disabled — nginx serves all."""
        firewall = self.bench.config.firewall
        if not firewall.enabled:
            return ""
        lines = [f"    {rule.action} {rule.ip};\n" for rule in firewall.rules]
        if firewall.default == "deny":
            lines.append("    deny all;\n")
        return "".join(lines) + "\n" if lines else ""

    def _xff_header(self) -> str:
        """Behind trusted proxies, pass their X-Forwarded-For through unchanged
        rather than appending the (proxy's own) connecting address to it."""
        return "$http_x_forwarded_for" if self._proxy_servers else "$proxy_add_x_forwarded_for"

    def is_installed(self) -> bool:
        return shutil.which("nginx") is not None

    def install(self) -> None:
        if not self.is_installed():
            get_package_manager().install("nginx")

    def generate_config(self, ssl_ready: bool = False) -> None:
        nginx_dir = self.bench.config_path / "nginx"
        sites_dir = nginx_dir / "sites"
        sites_dir.mkdir(parents=True, exist_ok=True)
        self._write_error_pages(nginx_dir)
        # admin.tls = False makes the whole bench HTTP-only: a central proxy
        # terminates TLS, so neither sites nor the admin serve HTTPS here.
        tls = self.bench.config.admin.tls
        for site in self.bench.sites():
            site_ssl_ready = tls and ssl_ready and self.cert_covers(site.config)
            conf_text = self._generate_site_config(site.config, site_ssl_ready)
            (sites_dir / f"{site.config.name}.conf").write_text(conf_text)
        # The admin is always reached via its (mandatory) domain in production;
        # nginx forwards that host to the local admin process.
        if self.bench.config.admin.domain:
            conf_text = self._generate_admin_config(ssl_ready)
            (sites_dir / "_admin.conf").write_text(conf_text)
        self._write_include_conf(nginx_dir)

    def _admin_socket_activated(self) -> bool:
        return self.bench.config.production.process_manager == "systemd"

    def _admin_proxy_port(self) -> int:
        """Where the admin actually listens: the socket-activated gunicorn's
        internal port under systemd, else the Flask process on admin.port."""
        admin = self.bench.config.admin
        return admin.internal_port if self._admin_socket_activated() else admin.port

    def _write_include_conf(self, nginx_dir: Path) -> None:
        bench_name = self.bench.config.name
        include_path = nginx_dir / "include.conf"
        include_path.write_text(
            self._render_upstream_block(bench_name)
            + f"include {nginx_dir}/sites/*.conf;\n"
        )

    def _generate_site_config(self, site: "SiteConfig", ssl_ready: bool) -> str:
        bench_name = self.bench.config.name
        nginx_config = self.bench.config.nginx
        bench_root = self.bench.path

        if not site.ssl or not ssl_ready:
            return self._render_http_only_block(
                site, bench_name, nginx_config, bench_root
            )

        return (
            self._render_http_redirect_block(site, nginx_config)
            + self._render_https_block(site, bench_name, nginx_config, bench_root)
        )

    def _error_pages_dir(self) -> Path:
        return self.bench.config_path / "nginx" / "error_pages"

    def _write_error_pages(self, nginx_dir: Path) -> None:
        error_dir = nginx_dir / "error_pages"
        error_dir.mkdir(parents=True, exist_ok=True)
        for code, (title, message) in _ERROR_PAGES.items():
            (error_dir / f"{code}.html").write_text(_render_error_html(code, title, message))

    def install_default_server(self) -> None:
        """Install the server-wide catch-all vhost (and its error pages) so
        requests for unknown hosts return our 404, not nginx's welcome page.
        Idempotent; shared by all benches."""
        staging = self.bench.config_path / "nginx"
        for code, (title, message) in _ERROR_PAGES.items():
            staged = staging / f"_catchall_{code}.html"
            staged.write_text(_render_error_html(code, title, message))
            run_command(_privileged(["install", "-D", "-m", "644", str(staged), str(_SHARED_ERROR_DIR / f"{code}.html")]))
            staged.unlink()

        staged = staging / "_catchall.conf"
        staged.write_text(
            self._render_catchall(
                self.bench.config.nginx.http_port,
                self.bench.config.nginx.https_port,
                _SHARED_ERROR_DIR,
            )
        )
        run_command(_privileged(["cp", str(staged), str(_catchall_conf())]))
        staged.unlink()

        # The distro's stock default site also claims default_server on :80;
        # nginx rejects a duplicate, so drop it and let ours win.
        for default_site in _stock_default_sites():
            if default_site.exists() or default_site.is_symlink():
                run_command(_privileged(["rm", "-f", str(default_site)]))

    def _render_catchall(self, http_port: int, https_port: int, error_dir: Path) -> str:
        directives = "".join(f"    error_page {code} /_errors/{code}.html;\n" for code in _ERROR_PAGES)
        return (
            # 256 fits any single server_name (DNS names max out at 253 chars); the
            # stock 64-byte bucket overflows on long custom/wildcard domains. Set once
            # here in the shared default conf — a per-bench copy would be a duplicate.
            "server_names_hash_bucket_size 256;\n\n"
            "server {\n"
            f"    listen {http_port} default_server;\n"
            f"    listen [::]:{http_port} default_server;\n"
            "    server_name _;\n\n"
            + directives
            + "    location ^~ /_errors/ {\n"
            + "        internal;\n"
            + f"        alias {error_dir}/;\n"
            + "    }\n\n"
            + "    location / {\n"
            + "        return 404;\n"
            + "    }\n"
            "}\n\n"
            # Without a default_server on the TLS port, an HTTPS request for a
            # host with no matching server block (e.g. an http-only bench reached
            # over https) silently falls through to the first 443 vhost defined,
            # serving the wrong bench's cert and content. ssl_reject_handshake
            # drops such handshakes outright (needs no certificate).
            "server {\n"
            f"    listen {https_port} ssl http2 default_server;\n"
            f"    listen [::]:{https_port} ssl http2 default_server;\n"
            "    server_name _;\n\n"
            "    ssl_reject_handshake on;\n"
            "}\n"
        )

    def _render_error_pages(self) -> str:
        directives = "".join(f"    error_page {code} /_errors/{code}.html;\n" for code in _ERROR_PAGES)
        return (
            directives
            + "    location ^~ /_errors/ {\n"
            + "        internal;\n"
            + "        allow all;\n"  # a firewall-denied client must still get its 403 page
            + f"        alias {self._error_pages_dir()}/;\n"
            + "    }\n\n"
        )

    def _render_upstream_block(self, bench_name: str) -> str:
        upstream_server = GunicornManager(self.bench).upstream_server()
        return (
            f"upstream bench-{bench_name} {{\n"
            f"    server {upstream_server};\n"
            f"    keepalive 32;\n"
            f"}}\n\n"
        )

    def _render_http_only_block(
        self,
        site: "SiteConfig",
        bench_name: str,
        nginx_config: object,
        bench_root: Path,
    ) -> str:
        server_name = " ".join(site.all_domains)
        max_body = nginx_config.client_max_body_size
        http_port = nginx_config.http_port
        socketio_port = self.bench.config.socketio_port

        return (
            f"server {{\n"
            f"    listen {http_port};\n"
            f"    listen [::]:{http_port};\n"
            f"    server_name {server_name};\n\n"
            + self._render_proxy_trust()
            + self._render_firewall()
            + f"    root {bench_root}/sites;\n"
            f"    client_max_body_size {max_body};\n\n"
            + self._render_acme_location()
            + self._render_error_pages()
            + self._render_assets_location()
            + self._render_files_location(site)
            + self._render_socketio_location(socketio_port, site.name)
            + self._render_proxy_location(bench_name, site)
            + f"}}\n"
        )

    def _render_http_redirect_block(self, site: "SiteConfig", nginx_config: object) -> str:
        server_name = " ".join(site.all_domains)
        http_port = nginx_config.http_port

        return (
            f"server {{\n"
            f"    listen {http_port};\n"
            f"    listen [::]:{http_port};\n"
            f"    server_name {server_name};\n\n"
            + self._render_proxy_trust()
            + self._render_firewall()
            + self._render_acme_location()
            + f"    location / {{\n"
            f"        return 301 https://$host$request_uri;\n"
            f"    }}\n"
            f"}}\n\n"
        )

    def _render_https_block(
        self,
        site: "SiteConfig",
        bench_name: str,
        nginx_config: object,
        bench_root: Path,
    ) -> str:
        server_name = " ".join(site.all_domains)
        https_port = nginx_config.https_port
        max_body = nginx_config.client_max_body_size
        socketio_port = self.bench.config.socketio_port
        cert = self.cert_path(site)
        key = Path("/etc/letsencrypt/live") / site.name / "privkey.pem"

        ssl_directives = (
            f"    ssl_certificate     {cert};\n"
            f"    ssl_certificate_key {key};\n"
            f"    ssl_protocols       TLSv1.2 TLSv1.3;\n"
            f"    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:"
            f"ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;\n"
            f"    ssl_prefer_server_ciphers off;\n"
            f"    ssl_session_cache   shared:SSL:10m;\n"
            f"    ssl_session_timeout 1d;\n\n"
        )

        return (
            f"server {{\n"
            f"    listen {https_port} ssl http2;\n"
            f"    listen [::]:{https_port} ssl http2;\n"
            f"    server_name {server_name};\n\n"
            + self._render_proxy_trust()
            + self._render_firewall()
            + ssl_directives
            + f"    root {bench_root}/sites;\n"
            f"    client_max_body_size {max_body};\n\n"
            + self._render_error_pages()
            + self._render_assets_location()
            + self._render_files_location(site)
            + self._render_socketio_location(socketio_port, site.name)
            + self._render_proxy_location(bench_name, site)
            + f"}}\n"
        )

    def _render_assets_location(self) -> str:
        return (
            f"    location /assets {{\n"
            f"        try_files $uri =404;\n"
            f"        expires 1y;\n"
            f'        add_header Cache-Control "public, immutable";\n'
            f"    }}\n\n"
        )

    def _render_files_location(self, site: "SiteConfig") -> str:
        return (
            f"    location ~ ^/files/.*\\.(jpg|jpeg|png|gif|svg|webp|pdf|docx?|xlsx?)$ {{\n"
            f"        root {self.bench.path}/sites/{site.name}/public;\n"
            f"        try_files $uri =404;\n"
            f"    }}\n\n"
        )

    def _render_socketio_location(self, socketio_port: int, site_name: str) -> str:
        # X-Frappe-Site-Name must be the site's real directory name, not $host:
        # a custom domain (host) differs from it and Frappe resolves the site by
        # this header. Host stays $host for URL building / host_name redirects.
        return (
            f"    location /socket.io {{\n"
            f"        proxy_pass         http://127.0.0.1:{socketio_port};\n"
            f"        proxy_http_version 1.1;\n"
            f"        proxy_set_header   Upgrade $http_upgrade;\n"
            f'        proxy_set_header   Connection "upgrade";\n'
            f"        proxy_set_header   X-Frappe-Site-Name {site_name};\n"
            f"        proxy_set_header   Origin $scheme://$http_host;\n"
            f"        proxy_set_header   Host $host;\n"
            f"    }}\n\n"
        )

    def _render_proxy_location(self, bench_name: str, site: "SiteConfig") -> str:
        # Send every non-primary host to the canonical (primary) domain. Scoped to
        # location / so /.well-known/acme-challenge/ (its own location) still works.
        # Only when a primary was explicitly chosen — otherwise site.primary falls
        # back to the (possibly internal) site name and would 301 public traffic to
        # an unreachable host.
        redirect = ""
        if len(site.all_domains) > 1 and site.primary_domain:
            redirect = (
                f'        if ($host != "{site.primary}") {{\n'
                f"            return 301 $scheme://{site.primary}$request_uri;\n"
                f"        }}\n"
            )
        return (
            f"    location / {{\n"
            + redirect
            + f"        proxy_pass         http://bench-{bench_name};\n"
            f"        proxy_read_timeout 120;\n"
            f"        proxy_redirect     off;\n"
            f"        proxy_set_header   Host               $host;\n"
            f"        proxy_set_header   X-Real-IP          $remote_addr;\n"
            f"        proxy_set_header   X-Forwarded-For    {self._xff_header()};\n"
            f"        proxy_set_header   X-Forwarded-Proto  $scheme;\n"
            f"        proxy_set_header   X-Frappe-Site-Name {site.name};\n"
            f"    }}\n"
        )

    def _generate_admin_config(self, ssl_ready: bool = False) -> str:
        admin = self.bench.config.admin
        nginx_config = self.bench.config.nginx
        http_port = nginx_config.http_port
        https_port = nginx_config.https_port
        domain = admin.domain

        acme_block = self._render_acme_location()
        firewall_block = self._render_firewall()
        proxy_block = self._render_error_pages() + self._render_admin_proxy_location()

        # admin.tls = False: a central proxy terminates TLS, so nginx serves the
        # admin over plain HTTP on :80 and never redirects to HTTPS, even if a
        # stale cert is still on disk.
        if not admin.tls:
            return (
                f"server {{\n"
                f"    listen {http_port};\n"
                f"    listen [::]:{http_port};\n"
                f"    server_name {domain};\n\n"
                + self._render_proxy_trust()
                + firewall_block
                + acme_block
                + proxy_block
                + f"}}\n"
            )

        if not ssl_ready or not self.admin_cert_exists():
            return (
                f"server {{\n"
                f"    listen {http_port};\n"
                f"    listen [::]:{http_port};\n"
                f"    server_name {domain};\n\n"
                + self._render_proxy_trust()
                + firewall_block
                + acme_block
                + proxy_block
                + f"}}\n"
            )

        cert = self.admin_cert_path()
        key = Path("/etc/letsencrypt/live") / domain / "privkey.pem"
        ssl_directives = (
            f"    ssl_certificate     {cert};\n"
            f"    ssl_certificate_key {key};\n"
            f"    ssl_protocols       TLSv1.2 TLSv1.3;\n"
            f"    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:"
            f"ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;\n"
            f"    ssl_prefer_server_ciphers off;\n"
            f"    ssl_session_cache   shared:SSL:10m;\n"
            f"    ssl_session_timeout 1d;\n\n"
        )
        return (
            f"server {{\n"
            f"    listen {http_port};\n"
            f"    listen [::]:{http_port};\n"
            f"    server_name {domain};\n\n"
            + self._render_proxy_trust()
            + firewall_block
            + acme_block
            + f"    location / {{\n"
            f"        return 301 https://$host$request_uri;\n"
            f"    }}\n"
            f"}}\n\n"
            f"server {{\n"
            f"    listen {https_port} ssl http2;\n"
            f"    listen [::]:{https_port} ssl http2;\n"
            f"    server_name {domain};\n\n"
            + self._render_proxy_trust()
            + firewall_block
            + ssl_directives
            + proxy_block
            + f"}}\n"
        )

    def _render_admin_proxy_location(self) -> str:
        return (
            f"    location / {{\n"
            f"        proxy_pass         http://127.0.0.1:{self._admin_proxy_port()};\n"
            f"        proxy_read_timeout 120;\n"
            f"        proxy_redirect     off;\n"
            f"        proxy_set_header   Host               $host;\n"
            f"        proxy_set_header   X-Real-IP          $remote_addr;\n"
            f"        proxy_set_header   X-Forwarded-For    {self._xff_header()};\n"
            f"        proxy_set_header   X-Forwarded-Proto  $scheme;\n"
            f"    }}\n"
        )

    def admin_cert_path(self) -> Path:
        return Path("/etc/letsencrypt/live") / self.bench.config.admin.domain / "fullchain.pem"

    def admin_cert_exists(self) -> bool:
        return self._cert_files_exist(Path("/etc/letsencrypt/live") / self.bench.config.admin.domain)

    def install_config(self) -> None:
        nginx_dir = self.bench.config.nginx.config_dir
        symlink_path = nginx_dir / f"{self.bench.config.name}.conf"
        source_path = self.bench.config_path / "nginx" / "include.conf"

        self._prune_dangling_symlinks(nginx_dir)
        if symlink_path.exists() or symlink_path.is_symlink():
            run_command(_privileged(["unlink", str(symlink_path)]))
        run_command(_privileged(["ln", "-s", str(source_path), str(symlink_path)]))
        self._set_worker_user()
        self.install_default_server()
        self._reload_or_rollback(symlink_path)

    @staticmethod
    def _prune_dangling_symlinks(nginx_dir: Path) -> None:
        """Remove any bench's vhost symlink whose target no longer exists.

        A bench dropped without going through its own teardown (e.g. its
        directory deleted directly) leaves its symlink here dangling; nginx -t
        then fails to open it and blocks every bench sharing this config dir,
        not just the one that was removed."""
        if not nginx_dir.is_dir():
            return
        for entry in nginx_dir.iterdir():
            if entry.is_symlink() and not entry.exists():
                run_command(_privileged(["unlink", str(entry)]))

    def _reload_or_rollback(self, symlink_path: Path) -> None:
        """A bad config for this one bench must not take nginx down for every
        other bench on the box — undo the symlink we just installed and let the
        caller see the original failure."""
        try:
            self.reload()
        except Exception:
            run_command(_privileged(["unlink", str(symlink_path)]))
            raise

    def _set_worker_user(self) -> None:
        """Run nginx workers as the bench owner. Idempotent."""
        owner = pwd.getpwuid(self.bench.path.stat().st_uid).pw_name
        directive = f"user {owner};"
        original = _NGINX_CONF.read_text()
        if _USER_DIRECTIVE.search(original):
            updated = _USER_DIRECTIVE.sub(directive, original, count=1)
        else:
            updated = directive + "\n" + original
        if updated == original:
            return
        staged = self.bench.config_path / "nginx" / "nginx.conf"
        staged.write_text(updated)
        run_command(_privileged(["cp", str(staged), str(_NGINX_CONF)]))
        staged.unlink()

    def uninstall_config(self) -> None:
        """Remove this bench's nginx vhosts (the symlink in the config dir), then
        validate and reload the remaining machine-wide config. Certs are kept."""
        symlink_path = self.bench.config.nginx.config_dir / f"{self.bench.config.name}.conf"
        if symlink_path.exists() or symlink_path.is_symlink():
            run_command(_privileged(["unlink", str(symlink_path)]))
        self.reload()

    def reload(self) -> None:
        run_command(_privileged(["nginx", "-t"]))
        if not is_linux():
            run_command(["nginx", "-s", "reload"])
            return
        # reload needs a running nginx; a fresh install may not be started yet.
        if is_alpine():
            run_command(service_enable_command("nginx"))
        action = "reload" if service_running("nginx") else "start"
        run_command(service_command(action, "nginx"))

    def cert_path(self, site: "SiteConfig") -> Path:
        return Path("/etc/letsencrypt/live") / site.name / "fullchain.pem"

    def cert_exists(self, site: "SiteConfig") -> bool:
        return self._cert_files_exist(Path("/etc/letsencrypt/live") / site.name)

    def cert_covers(self, site: "SiteConfig") -> bool:
        """True if a cert exists and, when the site has public domains, its SAN list
        covers every one — so a failed --expand can't serve a stale cert over HTTPS.
        Pure-.localhost sites have no public exposure, so cert existence is enough."""
        from pilot.managers.letsencrypt_manager import cert_covers, public_domains

        if not self.cert_exists(site):
            return False
        public = public_domains(site)
        return cert_covers(self.cert_path(site), public) if public else True

    @staticmethod
    def _cert_files_exist(live_dir: Path) -> bool:
        # /etc/letsencrypt/live is root-only (0700), so stat with privilege
        # rather than letting Path.exists() raise EACCES for the bench user.
        import subprocess

        return subprocess.run(
            _privileged(["test", "-f", str(live_dir / "fullchain.pem"), "-a", "-f", str(live_dir / "privkey.pem")]),
            capture_output=True,
        ).returncode == 0
