from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pilot.core.adapters.domain_provider import DomainRouteProvider

if TYPE_CHECKING:
    from pilot.config import RoutePolicy, SiteConfig
    from pilot.core.site import Site


class SiteDomains:
    def __init__(self, site: "Site") -> None:
        self.site = site
        self._provider = DomainRouteProvider(site.bench)

    def generate_dns_records(self, domain: str) -> dict:
        return self._provider.generate_dns_records(self.site.config.name, domain)

    def register(self, domain: str) -> "RoutePolicy | None":
        """Register a domain and return its provider route policy."""
        return self._provider.register(self.site.config.name, domain)

    def attach(self, domain: str, *, primary: bool = False) -> None:
        if not primary or not self.status(domain)[0]:
            self.register(domain)
        if primary:
            self.set_primary(domain)

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

    def describe(self) -> tuple[list[dict[str, str | bool]], str]:
        """Return all domains with their primary and public route state."""
        config = self._site_config()
        site_name = config.name
        primary = self.primary() or site_name
        rows = [
            self._description(config, domain, domain == primary)
            for domain in [site_name, *self.names()]
        ]
        return rows, primary

    def describe_domain(self, domain: str) -> dict[str, str | bool] | None:
        """Return one attached domain with its primary and public route state."""
        attached, is_primary = self.status(domain)
        if not attached:
            return None

        from pilot.utils import normalize_host

        config = self._site_config()
        normalized = normalize_host(domain)
        attached_domain = next(
            candidate for candidate in config.all_domains if normalize_host(candidate) == normalized
        )
        return self._description(config, attached_domain, is_primary)

    def apply_task(self, idempotency_key: str | None = None, email: str = "") -> str:
        """Regenerate nginx for the domain change, and issue certificates when the site needs
        them and has an email."""
        from pilot.tasks.setup_letsencrypt import SetupLetsEncryptTask
        from pilot.tasks.setup_nginx import SetupNginxTask

        if self.is_tls_enabled and (email or self.site.bench.config.letsencrypt.email):
            return SetupLetsEncryptTask.queue(self.site.bench, email=email, idempotency_key=idempotency_key)
        return SetupNginxTask.queue(self.site.bench, idempotency_key=idempotency_key)

    @property
    def is_certificate_email_required(self) -> bool:
        """Whether a new domain needs a Let's Encrypt email that the bench does not have yet."""
        bench_config = self.site.bench.config
        return bench_config.production.enabled and not bench_config.letsencrypt.email and self.is_tls_enabled

    @property
    def is_certificate_pending(self) -> bool:
        """Whether a certificate task for this bench is still queued or running."""
        from pilot.managers.task import TaskReader
        from pilot.tasks.setup_letsencrypt import SetupLetsEncryptTask

        return any(
            task.command == SetupLetsEncryptTask.command and task.status.is_active
            for task in TaskReader(self.site.bench.path).list_tasks(limit=None)
        )

    @property
    def is_tls_enabled(self) -> bool:
        try:
            config = json.loads((self.site.path / "site_config.json").read_text())
        except Exception:
            return False
        if not isinstance(config, dict):
            return False
        from pilot.config import RoutePolicy, SiteConfig

        site = SiteConfig(
            name=self.site.config.name,
            apps=[],
            ssl=bool(config.get("ssl")),
            domains=config.get("domains") or [],
            route=RoutePolicy.from_dict(config["route"]) if config.get("route") else None,
        )
        return bool(site.tls_domains)

    def _site_config(self) -> "SiteConfig":
        return next(
            site.config
            for site in self.site.bench.sites()
            if site.config.name == self.site.config.name
        )

    def _description(
        self,
        config: "SiteConfig",
        domain: str,
        is_primary: bool,
    ) -> dict[str, str | bool]:
        route = config.route_for(domain)
        tls = route.public_tls
        if tls and config.configured_route_for(domain) is None:
            from pilot.managers.nginx import NginxManager

            # A direct route serves TLS only once a certificate covers the domain.
            tls = domain in NginxManager(self.site.bench).serviceable_tls_domains(config, ssl_ready=True)
        return {
            "domain": domain,
            "is_site": domain == config.name,
            "is_primary": is_primary,
            "public_scheme": route.public_scheme,
            "tls": tls,
        }
