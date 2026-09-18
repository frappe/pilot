from dataclasses import dataclass, field

from pilot.config.route import RoutePolicy


@dataclass
class SiteDomain:
    """One hostname a site answers on, and where its TLS terminates."""

    name: str
    # None inherits the site's `ssl`. False is an edge-terminated domain that
    # reaches this host as plain HTTP; True ends TLS here.
    tls: bool | None = None
    route: RoutePolicy | None = None

    @classmethod
    def from_entry(cls, entry) -> "SiteDomain":
        """Accept a legacy hostname, a mapping, or a SiteDomain."""
        if isinstance(entry, SiteDomain):
            return entry
        if isinstance(entry, dict):
            tls = entry.get("tls")
            route = RoutePolicy.from_dict(entry["route"]) if entry.get("route") else None
            return cls(
                name=str(entry.get("domain") or ""),
                tls=None if tls is None else bool(tls),
                route=route,
            )
        return cls(name=str(entry))

    def to_entry(self) -> "str | dict":
        if self.route:
            return {"domain": self.name, "route": self.route.to_dict()}
        return self.name if self.tls is None else {"domain": self.name, "tls": self.tls}


@dataclass
class SiteConfig:
    name: str
    apps: list[str]
    admin_password: str | None = None
    domains: list[SiteDomain] = field(default_factory=list)
    ssl: bool = False
    default: bool = False
    primary_domain: str = ""
    # Certbot lineage name; empty means the current site name.
    cert_name: str = ""
    route: RoutePolicy | None = None

    def __post_init__(self) -> None:
        self.domains = [SiteDomain.from_entry(entry) for entry in self.domains if entry]

    @property
    def all_domains(self) -> list[str]:
        """Return the site name and unique aliases in order."""
        seen: set[str] = set()
        ordered: list[str] = []
        for domain in [self.name, *(entry.name for entry in self.domains)]:
            key = domain.strip().lower()
            if key and key not in seen:
                seen.add(key)
                ordered.append(domain)
        return ordered

    @property
    def primary(self) -> str:
        return self.primary_domain or self.name

    @property
    def has_released_certificate_pin(self) -> bool:
        """Whether the pinned certificate name is no longer a site domain."""
        if not self.cert_name:
            return False
        pinned = self.cert_name.strip().lower()
        return pinned not in {domain.strip().lower() for domain in self.all_domains}

    @property
    def certificate_name(self) -> str:
        """Return the safe certbot lineage name for this site."""
        from pilot.internal.validators import validate_hostname

        if self.cert_name and validate_hostname(self.cert_name) is None:
            return self.cert_name
        return self.name

    def terminates_tls(self, domain: str) -> bool:
        """Whether this host serves a domain over HTTPS."""
        for entry in self.domains:
            if entry.name == domain:
                if entry.route:
                    return entry.route.origin_tls
                return self.ssl if entry.tls is None else entry.tls
        return self.route.origin_tls if self.route else self.ssl

    def route_for(self, domain: str) -> RoutePolicy:
        """Return the configured route or the site's direct-route default."""
        return self.configured_route_for(domain) or RoutePolicy.direct(self.terminates_tls(domain))

    def configured_route_for(self, domain: str) -> RoutePolicy | None:
        """Return explicit route metadata for a site domain, if present."""
        for entry in self.domains:
            if entry.name == domain and entry.route:
                return entry.route
        if domain == self.name and self.route:
            return self.route
        return None

    def uses_tls(self, domain: str) -> bool:
        """Whether clients reach a site domain over HTTPS."""
        return self.route_for(domain).public_tls

    @property
    def tls_domains(self) -> list[str]:
        return [domain for domain in self.all_domains if self.terminates_tls(domain)]

    @property
    def plain_domains(self) -> list[str]:
        return [domain for domain in self.all_domains if not self.terminates_tls(domain)]
