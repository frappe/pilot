from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.managers import nginx_error_pages
from pilot.managers.gunicorn import GunicornManager
from pilot.managers.nginx_admin_render import NginxAdminConfigRenderer
from pilot.managers.nginx_error_pages import NginxErrorPages
from pilot.managers.nginx_site_locations import NginxSiteLocations
from pilot.managers.nginx_tls import live_cert_path, live_key_path, render_ssl_directives
from pilot.managers.nginx_waf_render import ModSecurityRenderer
from pilot.managers.waf import WafManager

ERROR_PAGES = nginx_error_pages.ERROR_PAGES
render_error_html = nginx_error_pages.render_error_html

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
        self._modsec = ModSecurityRenderer(bench)
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
        return render_ssl_directives(cert, key)

    def modsec_dir(self) -> Path:
        return self._modsec.modsec_dir()

    def _render_modsec_main(self, modsec_dir: Path) -> str:
        return self._modsec.render_main(modsec_dir)

    def _render_modsec_engine(self, waf: "WafConfig") -> str:
        return self._modsec.render_engine(waf)

    def _render_modsec_overrides(self, waf: "WafConfig") -> str:
        return self._modsec.render_overrides(waf)

    @staticmethod
    def _render_modsec_exclusions(waf: "WafConfig") -> str:
        return ModSecurityRenderer.render_exclusions(waf)

    @classmethod
    def _render_modsec_custom_rules(cls, waf: "WafConfig") -> str:
        return ModSecurityRenderer.render_custom_rules(waf)

    def _xff_header(self) -> str:
        """Behind a trusted proxy, pass its X-Forwarded-For through unchanged
        rather than appending our own connecting address to it."""
        return "$http_x_forwarded_for" if self._proxy_servers else "$proxy_add_x_forwarded_for"

    def cert_path(self, site: "SiteConfig") -> Path:
        return live_cert_path(site.name)

    def admin_cert_path(self) -> Path:
        return live_cert_path(self.bench.config.admin.domain)

    def generate_site_config(self, site: "SiteConfig", ssl_ready: bool) -> str:
        bench_name = self.bench.config.name
        nginx_config = self.bench.config.nginx
        bench_root = self.bench.path

        if not site.ssl or not ssl_ready:
            return self._render_http_only_block(site, bench_name, nginx_config, bench_root)

        return self._render_http_redirect_block(site, nginx_config) + self._render_https_block(
            site, bench_name, nginx_config, bench_root
        )

    def error_pages_dir(self) -> Path:
        return self.bench.config_path / "nginx" / "error_pages"

    def _render_catchall(self, http_port: int, https_port: int, error_dir: Path) -> str:
        return NginxErrorPages.render_catchall(http_port, https_port, error_dir)

    def _render_error_pages(self) -> str:
        return NginxErrorPages(self.error_pages_dir()).render_location()

    def _site_locations(self) -> NginxSiteLocations:
        return NginxSiteLocations(self.bench, self._xff_header())

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
        locations = self._site_locations()

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
            + locations.render_assets()
            + locations.render_files(site)
            + locations.render_socketio(socketio_port, site.name)
            + locations.render_proxy(bench_name, site)
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
        key = live_key_path(site.name)
        locations = self._site_locations()

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
            + locations.render_assets()
            + locations.render_files(site)
            + locations.render_socketio(socketio_port, site.name)
            + locations.render_proxy(bench_name, site)
            + "}\n"
        )

    def generate_admin_config(self, ssl_ready: bool = False, has_admin_cert: bool = False) -> str:
        return NginxAdminConfigRenderer(
            self.bench,
            security_trio=self._render_security_trio(),
            acme_location=self._render_acme_location(),
            error_pages=self._render_error_pages(),
            xff_header=self._xff_header(),
        ).render(ssl_ready, has_admin_cert)
