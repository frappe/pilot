from dataclasses import dataclass


@dataclass
class AdminConfig:
    port: int = 7000
    timeout: int = 180  # seconds
    enabled: bool = False
    password: str = ""
    domain: str = ""
    # Bench-wide TLS termination, opt-in. False (default): nginx serves sites and
    # the admin over plain HTTP on the http port and obtains no certs — the bench
    # is reachable as soon as production setup finishes, and a central proxy may
    # terminate TLS upstream. True: nginx terminates TLS here via Let's Encrypt
    # (HTTPS, with HTTP redirected to HTTPS for every TLS-enabled domain). Enable
    # it explicitly with `bench setup letsencrypt` or the admin Settings toggle.
    # It's a server-global choice carried forward to new benches.
    tls: bool = False

    @property
    def internal_port(self) -> int:
        """Localhost-only port that gunicorn binds (via the systemd socket) when
        the admin is socket-activated. nginx listens on `port` and forwards here.
        Derived for now; promote to a bench.toml field if it needs to be tunable."""
        return self.port + 1
