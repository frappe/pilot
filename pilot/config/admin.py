import re
from dataclasses import dataclass, field

from pilot.config.route import RoutePolicy
from pilot.exceptions import ConfigError

_HOSTNAME_PATTERN = re.compile(
    r"^(?=.{1,253}$)[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)


def default_allow_bench_management() -> bool:
    """Managing sibling benches is a development convenience. A release install keeps
    it off until an operator sets it in bench.toml."""
    from pilot import is_dev_build

    return is_dev_build


@dataclass
class AdminConfig:
    port: int = 7000  # New series not conflicting with sites
    timeout: int = 180  # seconds
    enabled: bool = False
    # A hash from pilot.internal.password_hash. Benches that predate the
    # hash_admin_password patch still hold cleartext, which verify_password accepts.
    password: str = ""
    jwt_secret: str = ""
    jwks_url: str = ""  # trust session tokens minted by a remote issuer publishing keys here
    # Required with jwks_url; binds remote tokens to this bench.
    jwks_audience: str = ""
    domain: str = ""
    tls: bool = False
    route: RoutePolicy | None = None
    allow_bench_management: bool = field(default_factory=default_allow_bench_management)
    # Break-glass codes for when no enrolled device is available. Stored in the clear so
    # an operator with server access can still read them; the API returns them only when
    # they are issued, never on demand.
    recovery_codes: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "AdminConfig":
        return cls(
            port=data.get("port", 7000),
            timeout=data.get("timeout", 180),
            enabled=data.get("enabled", False),
            password=data.get("password", ""),
            jwt_secret=data.get("jwt_secret", ""),
            jwks_url=data.get("jwks_url", ""),
            jwks_audience=data.get("jwks_audience", ""),
            domain=data.get("domain", ""),
            tls=data.get("tls", False),
            route=RoutePolicy.from_dict(data["route"]) if data.get("route") else None,
            allow_bench_management=data.get("allow_bench_management", default_allow_bench_management()),
            recovery_codes=list(data.get("recovery_codes", [])),
        )

    def set_password(self, password: str) -> None:
        """Store a new password, hashed. The cleartext is never written anywhere."""
        from pilot.internal.password_hash import hash_password, is_hashed

        self.password = password if is_hashed(password) else hash_password(password)

    def verify_password(self, password: str) -> bool:
        from pilot.internal.password_hash import verify_password

        return verify_password(password, self.password)

    @property
    def internal_port(self) -> int:
        """Localhost-only Gunicorn port behind nginx."""
        return self.port + 1

    @property
    def route_policy(self) -> RoutePolicy:
        return self.route or RoutePolicy.direct(self.tls)

    def validate(self, production_enabled: bool, bench_name: str) -> None:
        if not self.domain:
            if production_enabled:
                raise ConfigError(
                    f"admin.domain is required in production but is missing for bench '{bench_name}'. "
                    f"Set it in bench.toml (e.g. admin.example.com) or pass "
                    f"'pilot setup production --admin-domain <domain>'."
                )
            return
        if not _HOSTNAME_PATTERN.match(self.domain):
            raise ConfigError(f"admin.domain '{self.domain}' is not a valid hostname (bench '{bench_name}').")
