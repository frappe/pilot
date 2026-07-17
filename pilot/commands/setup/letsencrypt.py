from __future__ import annotations

from typing import TYPE_CHECKING

from pilot.commands.base import Command
from pilot.exceptions import ConfigError

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class SetupLetsEncryptCommand(Command):
    name = "letsencrypt"
    help = "Setup Let's Encrypt SSL."
    group = "setup"

    def __init__(self, bench: "Bench") -> None:
        from pilot.managers.letsencrypt import LetsEncryptManager
        from pilot.managers.nginx import NginxManager

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
