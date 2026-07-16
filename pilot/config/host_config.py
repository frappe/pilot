from dataclasses import dataclass


@dataclass
class HostConfig:
    """Shared state for every bench under one benches directory: one MariaDB
    and Postgres server, one ACME account, one trusted admin JWKS issuer, and
    which bench currently owns system-wide monitoring."""

    mariadb_port: int = 0
    mariadb_root_password: str = ""
    postgres_port: int = 0
    postgres_root_password: str = ""
    letsencrypt_email: str = ""
    admin_jwks_url: str = ""
    admin_jwks_audience: str = ""
    admin_tls: bool = False
    monitor_authority: str = ""
