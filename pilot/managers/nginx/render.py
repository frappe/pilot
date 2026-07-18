from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from pilot.internal.template import Template
from pilot.managers.gunicorn import GunicornManager
from pilot.managers.nginx.error_pages import ERROR_PAGES
from pilot.managers.nginx.tls import live_cert_path, live_key_path
from pilot.managers.waf import WafManager

if TYPE_CHECKING:
    from pilot.config import SiteConfig
    from pilot.core.bench import Bench

_TEMPLATES = Path(__file__).parent / "templates"
_BENCH_TEMPLATE = Template.from_path(_TEMPLATES / "bench.conf.template")
_SERVER_TEMPLATE = Template.from_path(_TEMPLATES / "server.conf.template")

_CORS_PATHS = ["/api/v1/health", "/api/v1/bootstrap"]


class NginxConfigRenderer:
    """Turns a bench into nginx config text. All layout and branching lives in
    templates/*.conf.template; this class only prepares the data they render
    from. NginxManager owns writing and reloading what this produces."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench
        self._proxy_servers_cache: list[str] | None = None

    def generate_bench_config(self, sites: list[tuple["SiteConfig", bool]], admin_ssl: bool) -> str:
        """The whole per-bench config: upstream, every site vhost, admin vhost.
        Each site is paired with whether its HTTPS cert is ready to serve."""
        vhosts = [self._site_vhost(site, ssl) for site, ssl in sites]
        if self.bench.config.admin.domain:
            vhosts.append(self._admin_vhost(admin_ssl))
        return _BENCH_TEMPLATE.render(**self._bench_context(vhosts))

    def generate_server_config(self, error_dir: Path) -> str:
        """The host-wide catch-all vhost, shared by every bench on the box."""
        nginx = self.bench.config.nginx
        return _SERVER_TEMPLATE.render(
            http_port=nginx.http_port,
            https_port=nginx.https_port,
            error_dir=error_dir,
            error_codes=list(ERROR_PAGES),
        )

    @property
    def _proxy_servers(self) -> list[str]:
        """Edge-proxy IPs in front of this bench, if any; looked up once."""
        if self._proxy_servers_cache is None:
            from pilot.core.adapters.domain_provider import DomainRouteProvider

            self._proxy_servers_cache = DomainRouteProvider.proxy_servers()
        return self._proxy_servers_cache

    def _is_waf_active(self) -> bool:
        """Gated on the module + CRS actually being installed, so a vhost
        never references an absent module (which would fail nginx -t)."""
        return self.bench.config.waf.enabled and WafManager.is_installed()

    def _site_vhost(self, site: "SiteConfig", ssl: bool) -> SimpleNamespace:
        canonical = site.primary if (len(site.all_domains) > 1 and site.primary_domain) else ""
        return SimpleNamespace(
            kind="site",
            server_name=" ".join(site.all_domains),
            ssl=ssl,
            cert=live_cert_path(site.name),
            key=live_key_path(site.name),
            name=site.name,
            public_root=f"{self.bench.path}/sites/{site.name}/public",
            canonical=canonical,
        )

    def _admin_vhost(self, ssl: bool) -> SimpleNamespace:
        admin = self.bench.config.admin
        socket_activated = self.bench.config.production.process_manager == "systemd"
        return SimpleNamespace(
            kind="admin",
            server_name=admin.domain,
            ssl=ssl,
            cert=live_cert_path(admin.domain),
            key=live_key_path(admin.domain),
            port=admin.internal_port if socket_activated else admin.port,
        )

    def _bench_context(self, vhosts: list[SimpleNamespace]) -> dict[str, Any]:
        config = self.bench.config
        nginx = config.nginx
        return {
            "upstream_name": config.name,
            "upstream_server": GunicornManager(self.bench).upstream_server,
            "http_port": nginx.http_port,
            "https_port": nginx.https_port,
            "client_max_body_size": nginx.client_max_body_size,
            "socketio_port": config.socketio_port,
            "sites_root": f"{self.bench.path}/sites",
            "acme_root": config.letsencrypt.webroot_path,
            "error_dir": self.bench.config_path / "nginx" / "error_pages",
            "error_codes": list(ERROR_PAGES),
            "proxy_servers": self._proxy_servers,
            "proxy_peers": "|".join(re.escape(ip) for ip in self._proxy_servers),
            "firewall": config.firewall,
            "waf_active": self._is_waf_active(),
            "waf_rules_file": self.bench.config_path / "modsecurity" / "main.conf",
            "cors_paths": _CORS_PATHS,
            "vhosts": vhosts,
        }
