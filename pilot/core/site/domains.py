from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pilot.core.adapters.domain_provider import DomainRouteProvider

if TYPE_CHECKING:
    from pilot.core.site import Site


class SiteDomains:
    def __init__(self, site: "Site") -> None:
        self.site = site
        self._provider = DomainRouteProvider(site.bench)

    def generate_dns_records(self, domain: str) -> dict:
        return self._provider.generate_dns_records(self.site.config.name, domain)

    def register(self, domain: str) -> None:
        self._provider.register(self.site.config.name, domain)

    def deregister(self, domain: str) -> None:
        self._provider.deregister(self.site.config.name, domain)

    def names(self) -> list[str]:
        return self._provider.domains(self.site.config.name)

    def primary(self) -> str | None:
        return self._provider.primary(self.site.config.name)

    def set_primary(self, domain: str | None) -> None:
        self._provider.set_primary(self.site.config.name, domain)

    def status(self, domain: str) -> tuple[bool, bool]:
        from pilot.utils import normalize_host

        normalized = normalize_host(domain)
        primary = self.primary()
        if normalized == normalize_host(self.site.config.name):
            return True, not primary or normalize_host(primary) == normalized
        attached = normalized in {normalize_host(name) for name in self.names()}
        return attached, primary is not None and normalize_host(primary) == normalized

    def apply_task(self, idempotency_key: str | None = None) -> str:
        from pilot.tasks.setup_letsencrypt import SetupLetsEncryptTask
        from pilot.tasks.setup_nginx import SetupNginxTask

        if self._is_ssl_enabled():
            return SetupLetsEncryptTask.queue(self.site.bench, idempotency_key=idempotency_key)
        return SetupNginxTask.queue(self.site.bench, idempotency_key=idempotency_key)

    def _is_ssl_enabled(self) -> bool:
        try:
            config = json.loads((self.site.path / "site_config.json").read_text())
        except Exception:
            return False
        return bool(config.get("ssl")) if isinstance(config, dict) else False
