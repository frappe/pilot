from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from pilot.commands.base import Command
from pilot.exceptions import ConfigError

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class SetupNginxCommand(Command):
    name = "nginx"
    help = "Generate nginx config."
    group = "setup"

    def __init__(self, bench: "Bench") -> None:
        from pilot.managers.nginx import NginxManager

        self.bench = bench
        self.nginx_manager = NginxManager(bench)

    def run(self) -> None:
        self._validate_nginx_enabled()
        self.nginx_manager.install()
        self._install_waf()
        self._ensure_nginx_config_directory()
        self.nginx_manager.generate_config(ssl_ready=True)
        self.nginx_manager.install_config()
        self._print_site_urls()

    def _install_waf(self) -> None:
        """Install the ModSecurity module + CRS as a standard part of production
        setup (like nginx itself), so enabling the WAF later is a fast, config-only
        change. Shared and machine-wide; idempotent, so re-runs are cheap.

        Best-effort: a package/download hiccup must not abort an otherwise-fine
        deploy — the WAF just stays unavailable (render is gated on is_installed)
        until a later setup run succeeds. Linux-only; a no-op elsewhere."""
        from pilot.managers.platform import is_linux

        if not is_linux():
            return
        from pilot.managers.waf_manager import WafManager

        try:
            WafManager(self.bench).install()
        except Exception as exc:
            print(
                f"Warning: could not install the WAF (ModSecurity/CRS): {exc}. "
                f"Sites are unaffected; re-run setup to retry.",
                file=sys.stderr,
            )

    def _validate_nginx_enabled(self) -> None:
        if not self.bench.config.production.enabled:
            raise ConfigError(
                "production.enabled must be true in bench.toml to run setup nginx. "
                "Production always uses nginx."
            )

    def _ensure_nginx_config_directory(self) -> None:
        nginx_dir = self.bench.config_path / "nginx"
        nginx_dir.mkdir(parents=True, exist_ok=True)

    def _print_site_urls(self) -> None:
        # HTTPS is only served when TLS termination is enabled for the bench; a
        # stale cert left on disk must not make us advertise an https:// URL.
        tls = self.bench.config.admin.tls
        for site in self.bench.sites():
            if tls and site.config.ssl and self.nginx_manager.cert_exists(site.config):
                print(f"  https://{site.config.name}")
            else:
                http_port = self.bench.config.nginx.http_port
                port_suffix = "" if http_port == 80 else f":{http_port}"
                print(f"  http://{site.config.name}{port_suffix}")
        domain = self.bench.config.admin.domain
        if domain:
            scheme = "https" if tls and self.nginx_manager.admin_cert_exists() else "http"
            print(f"  {scheme}://{domain} (admin)")
