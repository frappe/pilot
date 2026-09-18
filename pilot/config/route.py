from __future__ import annotations

from dataclasses import dataclass

from pilot.exceptions import ConfigError

PUBLIC_SCHEMES = frozenset({"http", "https"})
CLIENT_IP_SOURCES = frozenset({"direct", "x_forwarded_for", "proxy_protocol_v2"})


@dataclass(frozen=True)
class RoutePolicy:
    """How clients reach a hostname and how the provider reaches Pilot."""

    public_scheme: str
    origin_scheme: str
    client_ip_source: str

    @classmethod
    def from_dict(cls, data: object) -> "RoutePolicy":
        """Parse and validate a provider route policy."""
        if not isinstance(data, dict):
            raise ConfigError("The domain provider route policy must be a JSON object.")
        expected = {"public_scheme", "origin_scheme", "client_ip_source"}
        if set(data) != expected:
            raise ConfigError("The domain provider route policy has missing or unknown fields.")

        policy = cls(
            public_scheme=str(data.get("public_scheme") or ""),
            origin_scheme=str(data.get("origin_scheme") or ""),
            client_ip_source=str(data.get("client_ip_source") or ""),
        )
        policy.validate()
        return policy

    @classmethod
    def direct(cls, tls: bool) -> "RoutePolicy":
        """Build a route with no proxy between the client and Pilot."""
        scheme = "https" if tls else "http"
        return cls(scheme, scheme, "direct")

    def validate(self) -> None:
        """Reject unsupported schemes and client-address sources."""
        if self.public_scheme not in PUBLIC_SCHEMES:
            raise ConfigError("The route public_scheme must be 'http' or 'https'.")
        if self.origin_scheme not in PUBLIC_SCHEMES:
            raise ConfigError("The route origin_scheme must be 'http' or 'https'.")
        if self.client_ip_source not in CLIENT_IP_SOURCES:
            raise ConfigError(
                "The route client_ip_source must be 'direct', 'x_forwarded_for', "
                "or 'proxy_protocol_v2'."
            )

    @property
    def public_tls(self) -> bool:
        """Whether clients reach this route over HTTPS."""
        return self.public_scheme == "https"

    @property
    def origin_tls(self) -> bool:
        """Whether the provider reaches Pilot over HTTPS."""
        return self.origin_scheme == "https"

    @property
    def uses_proxy_protocol(self) -> bool:
        """Whether the origin connection uses PROXY protocol v2."""
        return self.client_ip_source == "proxy_protocol_v2"

    def to_dict(self) -> dict[str, str]:
        """Serialize this policy for site or bench configuration."""
        return {
            "public_scheme": self.public_scheme,
            "origin_scheme": self.origin_scheme,
            "client_ip_source": self.client_ip_source,
        }
