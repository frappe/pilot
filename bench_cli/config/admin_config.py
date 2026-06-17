from dataclasses import dataclass


@dataclass
class AdminConfig:
    port: int = 7000
    timeout: int = 180  # seconds
    enabled: bool = False
    password: str = ""
    domain: str = ""

    @property
    def internal_port(self) -> int:
        """Localhost-only port that gunicorn binds (via the systemd socket) when
        the admin is socket-activated. nginx listens on `port` and forwards here.
        Derived for now; promote to a bench.toml field if it needs to be tunable."""
        return self.port + 1
