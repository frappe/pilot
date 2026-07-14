from dataclasses import dataclass


@dataclass
class AdminConfig:
    port: int = 7000  # New series not conflicting with sites
    timeout: int = 180  # seconds
    enabled: bool = False
    password: str = ""
    jwt_secret: str = ""
    jwks_url: str = ""  # trust session tokens minted by a remote issuer publishing keys here
    jwks_audience: str = ""  # REQUIRED with jwks_url: remote tokens must carry a matching `aud` (per-bench binding); if empty, all remote tokens are rejected
    domain: str = ""
    tls: bool = False
    allow_bench_management: bool = True

    @property
    def internal_port(self) -> int:
        """Localhost-only port that gunicorn binds (via the systemd socket) when
        the admin is socket-activated. nginx listens on `port` and forwards here.
        Derived for now; promote to a bench.toml field if it needs to be tunable."""
        return self.port + 1
