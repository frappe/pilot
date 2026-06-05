from __future__ import annotations

from typing import TYPE_CHECKING

from bench_cli.exceptions import ConfigError
from bench_cli.managers.nginx_manager import NginxManager

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class SetupNginxCommand:
    def __init__(self, bench: "Bench") -> None:
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
        if not self.bench.config.production.nginx:
            raise ConfigError(
                "production.nginx must be true in bench.toml to run setup nginx."
            )

    def _ensure_nginx_config_directory(self) -> None:
        nginx_dir = self.bench.config_path / "nginx"
        nginx_dir.mkdir(parents=True, exist_ok=True)

    def _print_site_urls(self) -> None:
        for site in self.bench.sites():
            if site.config.ssl and self.nginx_manager.cert_exists(site.config):
                print(f"  https://{site.config.name}")
            else:
                http_port = self.bench.config.nginx.http_port
                port_suffix = "" if http_port == 80 else f":{http_port}"
                print(f"  http://{site.config.name}{port_suffix}")
