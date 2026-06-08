from __future__ import annotations

from typing import TYPE_CHECKING

from bench_cli.exceptions import ConfigError
from bench_cli.managers.letsencrypt_manager import LetsEncryptManager
from bench_cli.managers.nginx_manager import NginxManager

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class SetupLetsEncryptCommand:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench
        self.letsencrypt_manager = LetsEncryptManager(bench)
        self.nginx_manager = NginxManager(bench)

    def run(self) -> None:
        self._validate_email_set()
        self.letsencrypt_manager.install()
        self.letsencrypt_manager.ensure_webroot()
        # Ensure HTTP blocks exist for all domains so certbot can serve ACME challenges.
        self.nginx_manager.generate_config(ssl_ready=False)
        self.nginx_manager.reload()
        self.letsencrypt_manager.obtain_all()
        self.nginx_manager.generate_config(ssl_ready=True)
        self.nginx_manager.reload()

    def _validate_email_set(self) -> None:
        if not self.bench.config.letsencrypt.email:
            raise ConfigError(
                "letsencrypt.email must be set in bench.toml to run setup letsencrypt."
            )
