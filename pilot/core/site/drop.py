from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pilot.core.site import Site


class SiteDropper:
    def __init__(self, site: "Site") -> None:
        self.site = site

    def provider_domains(self) -> list[str]:
        """The site name and domains held by the route provider."""
        if not (self.site.path / "site_config.json").exists():
            return []
        return [self.site.config.name, *self.site.domains.names()]

    def release_domains(self, domains: list[str]) -> None:
        """Release the domain routes."""
        if not domains:
            return
        from pilot.core.adapters.domain_provider import DomainRouteProvider

        routes = DomainRouteProvider(self.site.bench)
        for domain in domains:
            routes.release(domain)

    def remove_from_bench_toml(self) -> None:
        """Remove the site from bench.toml."""
        from pilot.config import BenchConfig

        with BenchConfig.open(self.site.bench.path, mode="raw") as raw:
            raw["sites"] = [
                site for site in raw.get("sites", []) if site.get("name") != self.site.config.name
            ]
