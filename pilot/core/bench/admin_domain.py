from __future__ import annotations

from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.utils import host_owner, matches_wildcard, normalize_host

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class ProductionAdminDomain:
    def __init__(self, bench: "Bench", existing_domain: str) -> None:
        self.bench = bench
        self.existing_domain = existing_domain
        self.registered_domain: str | None = None

    def check(self) -> None:
        """Reject domains that are missing, already claimed, or outside the wildcard set."""
        from pilot.core.adapters.domain_provider import DomainRouteProvider

        domain = self.bench.config.admin.domain
        if not domain:
            return  # config validation raises the required-in-prod error with bench context
        owner = host_owner(self.bench.path, domain)
        if owner:
            raise BenchError(f"Admin domain '{domain}' is already used by bench '{owner}'.")
        target = normalize_host(domain)
        for site in self.bench.sites():
            if normalize_host(site.config.name) == target:
                raise BenchError(
                    f"Admin domain '{domain}' conflicts with this bench's own site '{site.config.name}'. "
                    f"An admin domain must not match a site domain."
                )
        if normalize_host(domain) == normalize_host(self.existing_domain):
            return
        patterns = DomainRouteProvider.wildcard_domains()
        if patterns and not matches_wildcard(domain, patterns):
            raise BenchError(
                f"Admin domain must match one of this bench's wildcard domains: {', '.join(patterns)}."
            )

    def register(self) -> None:
        """Provision a new/changed admin domain with the route provider."""
        from pilot.core.adapters.domain_provider import DomainRouteProvider

        self.registered_domain = None
        domain = self.bench.config.admin.domain
        if not domain or normalize_host(domain) == normalize_host(self.existing_domain):
            return
        DomainRouteProvider(self.bench).register(domain, domain)
        self.registered_domain = domain

    def rollback(self) -> None:
        """Release the just-registered admin route after a failed setup."""
        if not self.registered_domain:
            return
        from pilot.core.adapters.domain_provider import DomainRouteProvider

        DomainRouteProvider(self.bench).release(self.registered_domain)

    def release_previous(self) -> None:
        """Free the superseded admin hostname once the switch is committed."""
        if not self.registered_domain or not self.existing_domain:
            return
        from pilot.core.adapters.domain_provider import DomainRouteProvider

        DomainRouteProvider(self.bench).release(self.existing_domain)
