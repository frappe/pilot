from __future__ import annotations

from typing import TYPE_CHECKING

from bench_cli.commands.base import Command
from bench_cli.exceptions import ConfigError

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class SetupNginxCommand(Command):
    name = "nginx"
    help = "Generate nginx config."
    group = "setup"

    def __init__(self, bench: "Bench") -> None:
        from bench_cli.managers.nginx_manager import NginxManager

        self.bench = bench
        self.nginx_manager = NginxManager(bench)

    def run(self) -> None:
        self._validate_nginx_enabled()
        self.nginx_manager.install()
        self._ensure_nginx_config_directory()
        self.nginx_manager.generate_config(ssl_ready=True)
        self.nginx_manager.install_config()
        self.nginx_manager.reload()
        self._print_site_urls()

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
