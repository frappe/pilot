from __future__ import annotations

import pwd
import re
import shutil
import sys
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING

from pilot.managers.gunicorn import GunicornManager
from pilot.managers.packages import get_package_manager
from pilot.managers.waf import SHARED_MODSEC_DIR, WafManager
from pilot.managers.platform import (
    _privileged,
    default_nginx_config_dir,
    is_linux,
    service_command,
    service_running,
)
from pilot.utils import run_command

_NGINX_CONF = Path("/etc/nginx/nginx.conf")
_USER_DIRECTIVE = re.compile(r"^[ \t]*user[ \t]+[^;\n]+;", re.MULTILINE)

_SHARED_ERROR_DIR = Path("/usr/share/nginx/bench-error-pages")


def _catchall_conf() -> Path:
    """Shared default_server vhost so unknown hosts get our 404, not nginx's."""
    return default_nginx_config_dir() / "00-bench-default.conf"


def _stock_default_sites() -> list[Path]:
    """The distro's own default vhost(s), which would conflict with our catch-all."""
    return [Path("/etc/nginx/sites-enabled/default")]

# Custom pages for nginx-generated errors (downed upstream, missing static
# file). App responses pass through unchanged - proxy_intercept_errors is off.
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
<title>$code - $title</title>
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


def _cert_files_exist(live_dir: Path) -> bool:
    # /etc/letsencrypt/live is root-only (0700), so stat with privilege
    # rather than letting Path.exists() raise EACCES for the bench user.
    import subprocess

    return subprocess.run(
        _privileged(["test", "-f", str(live_dir / "fullchain.pem"), "-a", "-f", str(live_dir / "privkey.pem")]),
        capture_output=True,
    ).returncode == 0


if TYPE_CHECKING:
    from pilot.config.nginx import NginxConfig
    from pilot.config.site import SiteConfig
    from pilot.config.waf import WafConfig
    from pilot.core.bench import Bench


class NginxConfigRenderer:
    """Builds nginx vhost/admin config text for a bench. No filesystem writes
    or service control - NginxManager owns installing what this renders."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench
        self._proxy_servers_cache: list[str] | None = None

    @property
    def _proxy_servers(self) -> list[str]:
        """Edge-proxy IPs in front of this bench, if any; looked up once."""
        if self._proxy_servers_cache is None:
            from pilot.core.domains import DomainRouteProvider

            self._proxy_servers_cache = DomainRouteProvider.proxy_servers()
        return self._proxy_servers_cache

    def _render_acme_location(self) -> str:
        """`allow all;` overrides any firewall deny so certbot can validate."""
        webroot = self.bench.config.letsencrypt.webroot_path
        return (
            f"    location /.well-known/acme-challenge/ {{\n"
            f"        allow all;\n"
            f"        root {webroot};\n"
            f"        try_files $uri =404;\n"
            f"    }}\n\n"
        )

    def _render_proxy_trust(self) -> str:
        """Accept TCP only from trusted proxy IPs and read the real client IP
        from their X-Forwarded-For; empty (no restriction) when directly exposed.
        Tests $realip_remote_addr (the real TCP peer) so it runs before
        _render_firewall rewrites $remote_addr. Exempts the ACME path so a
        direct certbot hit still passes during setup."""
        proxies = self._proxy_servers
        if not proxies:
            return ""
        peers = "|".join(re.escape(ip) for ip in proxies)
        return (
            "".join(f"    set_real_ip_from   {ip};\n" for ip in proxies)
            + "    real_ip_header     X-Forwarded-For;\n"
            + "    real_ip_recursive  on;\n"
            + "    set $bench_from_proxy 0;\n"
            + f'    if ($realip_remote_addr ~ "^({peers})$") {{ set $bench_from_proxy 1; }}\n'
            + '    if ($request_uri ~ "^/\\.well-known/acme-challenge/") { set $bench_from_proxy 1; }\n'
            + "    if ($bench_from_proxy = 0) { return 403; }\n\n"
        )

    def _render_firewall(self) -> str:
        """First-match-wins allow/deny list. Trusted proxies are allowed first
        so a request without X-Forwarded-For (proxy IP in $remote_addr) can't
        be rejected by the configured rules. Empty when the firewall is off."""
        firewall = self.bench.config.firewall
        if not firewall.enabled:
            return ""
        lines = [f"    allow {ip};\n" for ip in self._proxy_servers]
        lines += [f"    {rule.action} {rule.ip};\n" for rule in firewall.rules]
        if firewall.default == "deny":
            lines.append("    deny all;\n")
        return "".join(lines) + "\n" if lines else ""

    def _waf_active(self) -> bool:
        """Gated on the module + CRS actually being installed, so a vhost
        never references an absent module (which would fail nginx -t)."""
        return self.bench.config.waf.enabled and WafManager.is_installed()

    def _render_waf(self) -> str:
        """Points nginx at this bench's generated ModSecurity rules file."""
        if not self._waf_active():
            return ""
        rules_file = self.modsec_dir() / "main.conf"
        return f"    modsecurity on;\n    modsecurity_rules_file {rules_file};\n\n"

    def _render_security_trio(self) -> str:
        return self._render_proxy_trust() + self._render_firewall() + self._render_waf()

    @staticmethod
    def _render_ssl_directives(cert: Path, key: Path) -> str:
        return (
            f"    ssl_certificate     {cert};\n"
            f"    ssl_certificate_key {key};\n"
            f"    ssl_protocols       TLSv1.2 TLSv1.3;\n"
            f"    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:"
            f"ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;\n"
            f"    ssl_prefer_server_ciphers off;\n"
            f"    ssl_session_cache   shared:SSL:10m;\n"
            f"    ssl_session_timeout 1d;\n\n"
        )

    def modsec_dir(self) -> Path:
        return self.bench.config_path / "modsecurity"

    def _render_modsec_main(self, modsec_dir: Path) -> str:
        """Chain order CRS requires: engine, CRS baseline, overrides, CRS rules, exclusions."""
        return (
            f"Include {modsec_dir}/modsecurity.conf\n"
            f"Include {SHARED_MODSEC_DIR}/crs-setup.conf\n"
            f"Include {modsec_dir}/overrides.conf\n"
            f"Include {SHARED_MODSEC_DIR}/rules/*.conf\n"
            f"Include {modsec_dir}/exclusions.conf\n"
        )

    def _render_modsec_engine(self, waf: "WafConfig") -> str:
        from pilot.config.waf import parse_nginx_size

        audit_log = self.bench.path / "logs" / "modsec_audit.log"
        # DetectionOnly must never block, so oversized bodies are inspected in part
        # rather than rejected; only full enforcement rejects them.
        body_action = "Reject" if waf.mode == "On" else "ProcessPartial"
        response_access = "On" if waf.inspect_responses else "Off"
        return (
            f"SecRuleEngine {waf.mode}\n"
            "SecRequestBodyAccess On\n"
            f"SecRequestBodyLimit {parse_nginx_size(waf.body_limit)}\n"
            "SecRequestBodyNoFilesLimit 131072\n"
            f"SecRequestBodyLimitAction {body_action}\n"
            "SecRequestBodyJsonDepthLimit 512\n"
            f"SecResponseBodyAccess {response_access}\n"
            "SecResponseBodyMimeType text/plain text/html text/xml application/json\n"
            "SecResponseBodyLimit 524288\n"
            "SecAuditEngine RelevantOnly\n"
            "SecAuditLogFormat JSON\n"
            "SecAuditLogType Serial\n"
            f"SecAuditLog {audit_log}\n"
            "SecAuditLogParts ABIJDEFHZ\n"
            "SecTmpDir /tmp\n"
            "SecDataDir /tmp\n"
            'SecDefaultAction "phase:1,pass,log"\n'
            'SecDefaultAction "phase:2,pass,log"\n'
        )

    def _render_modsec_overrides(self, waf: "WafConfig") -> str:
        """Per-bench CRS tuning, applied after crs-setup.conf so it wins.
        Rule ids start at 1000 to stay clear of CRS's reserved range."""
        lines = [
            f'SecAction "id:1000,phase:1,pass,nolog,'
            f"setvar:tx.blocking_paranoia_level={waf.paranoia},"
            f"setvar:tx.detection_paranoia_level={waf.paranoia},"
            f'setvar:tx.paranoia_level={waf.paranoia}"',
            f'SecAction "id:1001,phase:1,pass,nolog,'
            f'setvar:tx.inbound_anomaly_score_threshold={waf.inbound_threshold}"',
        ]
        for index, path in enumerate(waf.exempt_paths):
            lines.append(
                f'SecRule REQUEST_URI "@beginsWith {path}" '
                f'"id:{10000 + index},phase:1,pass,nolog,ctl:ruleEngine=Off"'
            )
        return "\n".join(lines) + "\n"

    @staticmethod
    def _render_modsec_exclusions(waf: "WafConfig") -> str:
        """User SecLang lines (SecRuleRemoveById etc.), one per line."""
        return "\n".join(waf.exclusions) + ("\n" if waf.exclusions else "")

    def _xff_header(self) -> str:
        """Behind a trusted proxy, pass its X-Forwarded-For through unchanged
        rather than appending our own connecting address to it."""
        return "$http_x_forwarded_for" if self._proxy_servers else "$proxy_add_x_forwarded_for"

    def cert_path(self, site: "SiteConfig") -> Path:
        return Path("/etc/letsencrypt/live") / site.name / "fullchain.pem"

    def admin_cert_path(self) -> Path:
        return Path("/etc/letsencrypt/live") / self.bench.config.admin.domain / "fullchain.pem"

    def generate_site_config(self, site: "SiteConfig", ssl_ready: bool) -> str:
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

    def error_pages_dir(self) -> Path:
        return self.bench.config_path / "nginx" / "error_pages"

    def _render_catchall(self, http_port: int, https_port: int, error_dir: Path) -> str:
        directives = "".join(f"    error_page {code} /_errors/{code}.html;\n" for code in _ERROR_PAGES)
        return (
            # 256 fits any server_name; the stock 64-byte bucket overflows on
            # long custom/wildcard domains. Set once here, not per-bench.
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
            # Without this, an https:// request for an http-only bench falls
            # through to the first 443 vhost and serves the wrong cert.
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
            + f"        alias {self.error_pages_dir()}/;\n"
            + "    }\n\n"
        )

    def _render_upstream_block(self, bench_name: str) -> str:
        upstream_server = GunicornManager(self.bench).upstream_server
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
        nginx_config: "NginxConfig",
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
            + self._render_security_trio()
            + f"    root {bench_root}/sites;\n"
            f"    client_max_body_size {max_body};\n\n"
            + self._render_acme_location()
            + self._render_error_pages()
            + self._render_assets_location()
            + self._render_files_location(site)
            + self._render_socketio_location(socketio_port, site.name)
            + self._render_proxy_location(bench_name, site)
            + "}\n"
        )

    def _render_http_redirect_block(self, site: "SiteConfig", nginx_config: "NginxConfig") -> str:
        server_name = " ".join(site.all_domains)
        http_port = nginx_config.http_port

        return (
            f"server {{\n"
            f"    listen {http_port};\n"
            f"    listen [::]:{http_port};\n"
            f"    server_name {server_name};\n\n"
            + self._render_security_trio()
            + self._render_acme_location()
            + "    location / {\n"
            "        return 301 https://$host$request_uri;\n"
            "    }\n"
            "}\n\n"
        )

    def _render_https_block(
        self,
        site: "SiteConfig",
        bench_name: str,
        nginx_config: "NginxConfig",
        bench_root: Path,
    ) -> str:
        server_name = " ".join(site.all_domains)
        https_port = nginx_config.https_port
        max_body = nginx_config.client_max_body_size
        socketio_port = self.bench.config.socketio_port
        cert = self.cert_path(site)
        key = Path("/etc/letsencrypt/live") / site.name / "privkey.pem"

        return (
            f"server {{\n"
            f"    listen {https_port} ssl http2;\n"
            f"    listen [::]:{https_port} ssl http2;\n"
            f"    server_name {server_name};\n\n"
            + self._render_security_trio()
            + self._render_ssl_directives(cert, key)
            + f"    root {bench_root}/sites;\n"
            f"    client_max_body_size {max_body};\n\n"
            + self._render_error_pages()
            + self._render_assets_location()
            + self._render_files_location(site)
            + self._render_socketio_location(socketio_port, site.name)
            + self._render_proxy_location(bench_name, site)
            + "}\n"
        )

    def _render_assets_location(self) -> str:
        return (
            "    location /assets {\n"
            "        try_files $uri =404;\n"
            "        expires 1y;\n"
            '        add_header Cache-Control "public, immutable";\n'
            "    }\n\n"
        )

    def _render_files_location(self, site: "SiteConfig") -> str:
        return (
            f"    location ~ ^/files/.*\\.(jpg|jpeg|png|gif|svg|webp|pdf|docx?|xlsx?)$ {{\n"
            f"        root {self.bench.path}/sites/{site.name}/public;\n"
            f"        try_files $uri =404;\n"
            f"    }}\n\n"
        )

    def _render_socketio_location(self, socketio_port: int, site_name: str) -> str:
        # X-Frappe-Site-Name is the site's real directory name, not $host (a
        # custom domain), since Frappe resolves the site by this header.
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
        # Redirect non-primary hosts to the primary domain, only when one was
        # explicitly chosen - else site.primary falls back to the (possibly
        # internal) site name and would 301 public traffic to an unreachable host.
        redirect = ""
        if len(site.all_domains) > 1 and site.primary_domain:
            redirect = (
                f'        if ($host != "{site.primary}") {{\n'
                f"            return 301 $scheme://{site.primary}$request_uri;\n"
                f"        }}\n"
            )
        return (
            "    location / {\n"
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

    def generate_admin_config(self, ssl_ready: bool = False, has_admin_cert: bool = False) -> str:
        admin = self.bench.config.admin
        nginx_config = self.bench.config.nginx
        http_port = nginx_config.http_port
        https_port = nginx_config.https_port
        domain = admin.domain

        acme_block = self._render_acme_location()
        security_trio = self._render_security_trio()
        proxy_block = (
            self._render_error_pages()
            + self._render_open_cors_location("/api/v1/health")
            + self._render_open_cors_location("/api/v1/bootstrap")
            + self._render_admin_proxy_location()
        )

        # admin.tls off, or no cert yet: plain HTTP, never redirect to HTTPS.
        if not admin.tls or not ssl_ready or not has_admin_cert:
            return self._render_admin_http_block(http_port, domain, security_trio, acme_block, proxy_block)

        cert = self.admin_cert_path()
        key = Path("/etc/letsencrypt/live") / domain / "privkey.pem"
        return self._render_admin_https_redirect_block(
            http_port, domain, security_trio, acme_block
        ) + self._render_admin_https_block(https_port, domain, security_trio, cert, key, proxy_block)

    def _render_admin_http_block(
        self, http_port: int, domain: str, security_trio: str, acme_block: str, proxy_block: str
    ) -> str:
        return (
            f"server {{\n"
            f"    listen {http_port};\n"
            f"    listen [::]:{http_port};\n"
            f"    server_name {domain};\n\n"
            + security_trio
            + acme_block
            + proxy_block
            + "}\n"
        )

    def _render_admin_https_redirect_block(
        self, http_port: int, domain: str, security_trio: str, acme_block: str
    ) -> str:
        return (
            f"server {{\n"
            f"    listen {http_port};\n"
            f"    listen [::]:{http_port};\n"
            f"    server_name {domain};\n\n"
            + security_trio
            + acme_block
            + f"    location / {{\n"
            f"        return 301 https://$host$request_uri;\n"
            f"    }}\n"
            f"}}\n\n"
        )

    def _render_admin_https_block(
        self, https_port: int, domain: str, security_trio: str, cert: Path, key: Path, proxy_block: str
    ) -> str:
        return (
            f"server {{\n"
            f"    listen {https_port} ssl http2;\n"
            f"    listen [::]:{https_port} ssl http2;\n"
            f"    server_name {domain};\n\n"
            + security_trio
            + self._render_ssl_directives(cert, key)
            + proxy_block
            + "}\n"
        )

    def _render_open_cors_location(self, path: str) -> str:
        """Probed cross-origin (e.g. ReconnectOverlay after a scheme change),
        so nginx answers with a wide-open CORS header regardless of the app."""
        return (
            f"    location = {path} {{\n"
            f"        proxy_pass         http://127.0.0.1:{self._admin_proxy_port()};\n"
            f"        proxy_read_timeout 120;\n"
            f"        proxy_redirect     off;\n"
            f"        proxy_set_header   Host               $host;\n"
            f"        proxy_set_header   X-Real-IP          $remote_addr;\n"
            f"        proxy_set_header   X-Forwarded-For    {self._xff_header()};\n"
            f"        proxy_set_header   X-Forwarded-Proto  $scheme;\n"
            f"        proxy_hide_header  Access-Control-Allow-Origin;\n"
            f"        add_header         Access-Control-Allow-Origin * always;\n"
            f"    }}\n"
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

    def _admin_socket_activated(self) -> bool:
        return self.bench.config.production.process_manager == "systemd"

    def _admin_proxy_port(self) -> int:
        """Socket-activated gunicorn's internal port under systemd, else admin.port."""
        admin = self.bench.config.admin
        return admin.internal_port if self._admin_socket_activated() else admin.port


class NginxManager:
    """Installs, configures, and reloads nginx for a bench, via NginxConfigRenderer."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench
        self._renderer = NginxConfigRenderer(bench)

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
        self._write_waf_files()
        # admin.tls = False makes the whole bench HTTP-only: a central proxy
        # terminates TLS, so neither sites nor the admin serve HTTPS here.
        tls = self.bench.config.admin.tls
        for site in self.bench.sites():
            site_ssl_ready = tls and ssl_ready and self.cert_covers(site.config)
            conf_text = self._renderer.generate_site_config(site.config, site_ssl_ready)
            (sites_dir / f"{site.config.name}.conf").write_text(conf_text)
        # The admin is always reached via its (mandatory) domain in production;
        # nginx forwards that host to the local admin process.
        if self.bench.config.admin.domain:
            conf_text = self._renderer.generate_admin_config(ssl_ready, self.has_admin_cert)
            (sites_dir / "_admin.conf").write_text(conf_text)
        self._write_include_conf(nginx_dir)

    def reload_for_site_change(self) -> None:
        if not self.bench.config.production.enabled or not self.is_installed():
            return
        print("Updating nginx configuration...")
        sys.stdout.flush()
        self.generate_config(ssl_ready=True)
        self.reload()

    def _write_include_conf(self, nginx_dir: Path) -> None:
        bench_name = self.bench.config.name
        include_path = nginx_dir / "include.conf"
        include_path.write_text(
            self._renderer._render_upstream_block(bench_name)
            + f"include {nginx_dir}/sites/*.conf;\n"
        )

    def _write_error_pages(self, nginx_dir: Path) -> None:
        error_dir = nginx_dir / "error_pages"
        error_dir.mkdir(parents=True, exist_ok=True)
        for code, (title, message) in _ERROR_PAGES.items():
            (error_dir / f"{code}.html").write_text(_render_error_html(code, title, message))

    def install_default_server(self) -> None:
        """Install the shared catch-all vhost and error pages. Idempotent."""
        staging = self.bench.config_path / "nginx"
        for code, (title, message) in _ERROR_PAGES.items():
            staged = staging / f"_catchall_{code}.html"
            staged.write_text(_render_error_html(code, title, message))
            run_command(_privileged(["install", "-D", "-m", "644", str(staged), str(_SHARED_ERROR_DIR / f"{code}.html")]))
            staged.unlink()

        staged = staging / "_catchall.conf"
        staged.write_text(
            self._renderer._render_catchall(
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

    def _write_waf_files(self) -> None:
        """Write this bench's ModSecurity rule files. No-op when the WAF is off;
        the CRS baseline itself is shared, installed by WafManager."""
        waf = self.bench.config.waf
        if not waf.enabled:
            return
        modsec_dir = self._renderer.modsec_dir()
        modsec_dir.mkdir(parents=True, exist_ok=True)
        (self.bench.path / "logs").mkdir(exist_ok=True)
        (modsec_dir / "modsecurity.conf").write_text(self._renderer._render_modsec_engine(waf))
        (modsec_dir / "overrides.conf").write_text(self._renderer._render_modsec_overrides(waf))
        (modsec_dir / "exclusions.conf").write_text(self._renderer._render_modsec_exclusions(waf))
        (modsec_dir / "main.conf").write_text(self._renderer._render_modsec_main(modsec_dir))

    def _config_dir(self) -> Path:
        return self.bench.config.nginx.config_dir or default_nginx_config_dir()

    def install_config(self) -> None:
        nginx_dir = self._config_dir()
        symlink_path = nginx_dir / f"{self.bench.config.name}.conf"
        source_path = self.bench.config_path / "nginx" / "include.conf"

        self._prune_dangling_symlinks(nginx_dir)
        if symlink_path.exists() or symlink_path.is_symlink():
            run_command(_privileged(["unlink", str(symlink_path)]))
        run_command(_privileged(["ln", "-s", str(source_path), str(symlink_path)]))
        self._set_worker_user()
        if self.bench.config.waf.enabled:
            self._ensure_modsecurity_module()
        self.install_default_server()
        self._reload_or_rollback(symlink_path)

    def _ensure_modsecurity_module(self) -> None:
        """Debian auto-enables the module; elsewhere inject a load_module line.
        No-op when not installed - the reload's nginx -t catches that."""
        if self._module_already_loaded():
            return
        module_path = WafManager.module_path()
        if module_path is None:
            return
        original = _NGINX_CONF.read_text()
        updated = f"load_module {module_path};\n" + original
        staged = self.bench.config_path / "nginx" / "nginx.conf"
        staged.write_text(updated)
        run_command(_privileged(["cp", str(staged), str(_NGINX_CONF)]))
        staged.unlink()

    @staticmethod
    def _module_already_loaded() -> bool:
        """A load_module line or modules-enabled drop-in, not just the .so on
        disk. Unreadable nginx.conf (unprivileged bench) counts as loaded -
        nginx -t is the authoritative check either way."""
        try:
            if "ngx_http_modsecurity_module" in _NGINX_CONF.read_text():
                return True
        except OSError:
            return True
        modules_dir = Path("/etc/nginx/modules-enabled")
        return modules_dir.is_dir() and any("modsecurity" in entry.name for entry in modules_dir.iterdir())

    @staticmethod
    def _prune_dangling_symlinks(nginx_dir: Path) -> None:
        """A bench dropped without its own teardown leaves a dangling symlink
        here, which fails nginx -t for every bench sharing this config dir."""
        if not nginx_dir.is_dir():
            return
        for entry in nginx_dir.iterdir():
            if entry.is_symlink() and not entry.exists():
                run_command(_privileged(["unlink", str(entry)]))

    def _reload_or_rollback(self, symlink_path: Path) -> None:
        """A bad config for this bench must not take nginx down for every
        other bench on the box - undo the symlink and re-raise."""
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
        """Remove this bench's vhost symlink and reload. Certs are kept."""
        symlink_path = self._config_dir() / f"{self.bench.config.name}.conf"
        if symlink_path.exists() or symlink_path.is_symlink():
            run_command(_privileged(["unlink", str(symlink_path)]))
        self.reload()

    def reload(self) -> None:
        run_command(_privileged(["nginx", "-t"]), timeout=10)
        if not is_linux():
            run_command(["nginx", "-s", "reload"])
            return
        # reload needs a running nginx; a fresh install may not be started yet.
        action = "reload" if service_running("nginx") else "start"
        run_command(service_command(action, "nginx"))

    def cert_path(self, site: "SiteConfig") -> Path:
        return Path("/etc/letsencrypt/live") / site.name / "fullchain.pem"

    def has_cert(self, site: "SiteConfig") -> bool:
        return _cert_files_exist(Path("/etc/letsencrypt/live") / site.name)

    def cert_covers(self, site: "SiteConfig") -> bool:
        """Cert exists and its SAN list covers every public domain, if any -
        so a failed --expand can't serve a stale cert over HTTPS."""
        from pilot.managers.letsencrypt import cert_covers, public_domains

        if not self.has_cert(site):
            return False
        public = public_domains(site)
        return cert_covers(self.cert_path(site), public) if public else True

    def admin_cert_path(self) -> Path:
        return Path("/etc/letsencrypt/live") / self.bench.config.admin.domain / "fullchain.pem"

    @property
    def has_admin_cert(self) -> bool:
        return _cert_files_exist(Path("/etc/letsencrypt/live") / self.bench.config.admin.domain)
