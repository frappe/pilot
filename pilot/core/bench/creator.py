from __future__ import annotations

import secrets
import socket
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchAlreadyExistsError
from pilot.utils import iter_sibling_benches

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class BenchCreator:
    """Creates a bench and inherits server-wide settings from siblings."""

    def __init__(
        self,
        target_directory: Path,
        name: str,
        process_manager: str = "",
        admin_domain: str = "",
        admin_tls: bool | None = None,
        db_type: str = "mariadb",
    ) -> None:
        self.target_directory = target_directory
        self.name = name
        self.process_manager = process_manager
        self.admin_domain = admin_domain
        # None → inherit the server-wide value from a sibling bench (default False).
        self.admin_tls = admin_tls
        self.db_type = db_type

    def run(self, on_progress: Callable[[str], None] = lambda message: None) -> "Bench":
        from pilot.config.bench_toml_builder import default_ports
        from pilot.config import BenchTomlStore
        from pilot.core.bench import Bench

        bench_toml = self.target_directory / "bench.toml"
        if bench_toml.exists():
            raise BenchAlreadyExistsError(f"Bench '{self.name}' already exists.")

        benches_dir = self.target_directory.parent
        if not benches_dir.exists():
            on_progress(f"Creating benches directory at {benches_dir}")
            benches_dir.mkdir(parents=True, exist_ok=True)

        on_progress(f"Creating bench directory: {self.target_directory}")
        self.target_directory.mkdir(parents=True, exist_ok=True)

        offset = self._pick_port_offset(self.target_directory)
        on_progress("Writing bench.toml")
        admin_tls = self.admin_tls if self.admin_tls is not None else self._sibling_admin_tls()
        # admin.domain is left empty unless given: development serves the admin on
        # localhost, and 'bench setup production' requires a real domain (via its
        # --admin-domain flag or here), erroring rather than deploying a placeholder.
        settings = {
            "admin_enabled": True,
            "admin_domain": self.admin_domain,
            "admin_tls": admin_tls,
            "db_type": self.db_type,
        }
        if self.db_type == "mariadb":
            settings["mariadb_port"] = self._sibling_mariadb_port() or self._pick_mariadb_port()
            settings["mariadb_password"] = self._sibling_mariadb_password() or secrets.token_hex(
                nbytes=8
            )
        if self.db_type == "postgres":
            settings["postgres_port"] = self._sibling_postgres_port() or self._pick_postgres_port()
            settings["postgres_password"] = self._sibling_postgres_password() or secrets.token_hex(
                nbytes=8
            )
        if self.process_manager:
            settings["production_process_manager"] = self.process_manager

        sibling_email = self._sibling_letsencrypt_email()
        if sibling_email:
            settings["letsencrypt_email"] = sibling_email

        sibling_admin = self._sibling_jwks_admin()
        if sibling_admin:
            settings["admin_jwks_url"] = sibling_admin.jwks_url
            if sibling_admin.jwks_audience:
                settings["admin_jwks_audience"] = sibling_admin.jwks_audience

        BenchTomlStore(bench_toml).write_flat(self.name, settings, port_offset=offset)

        admin_port = default_ports()["admin.port"] + offset
        on_progress(f"\nBench '{self.name}' created at {self.target_directory}")
        on_progress("\nNext step:")
        on_progress("  bench start")
        on_progress(f"  Then open http://localhost:{admin_port} — the setup wizard takes it from there.")

        return Bench(self.target_directory)

    def _sibling_letsencrypt_email(self) -> str:
        """Return the shared Let's Encrypt email from a sibling bench."""
        for _, config in iter_sibling_benches(self.target_directory):
            email = getattr(config.letsencrypt, "email", "")
            if email:
                return email
        return ""

    def _sibling_mariadb_port(self) -> int:
        """Return the shared MariaDB port from a sibling bench."""
        for _, config in iter_sibling_benches(self.target_directory):
            if config.db_type == "mariadb" and config.mariadb.port:
                return config.mariadb.port
        return 0

    def _pick_mariadb_port(self) -> int:
        """Pick the first free MariaDB port for the first bench on this host."""
        from pilot.config import MariaDBConfig
        from pilot.managers.platform import is_macos

        port = MariaDBConfig().port
        if is_macos():
            return port
        while self._port_is_live(port):
            port += 1
        return port

    def _sibling_mariadb_password(self) -> str:
        """Return the shared MariaDB root password from a sibling bench."""
        for _, config in iter_sibling_benches(self.target_directory):
            if config.db_type == "mariadb" and config.mariadb.root_password:
                return config.mariadb.root_password
        return ""

    def _sibling_postgres_port(self) -> int:
        """Return the shared PostgreSQL port from a sibling bench."""
        for _, config in iter_sibling_benches(self.target_directory):
            if config.db_type == "postgres" and config.postgres.port:
                return config.postgres.port
        return 0

    def _pick_postgres_port(self) -> int:
        """Pick the first free PostgreSQL port for the first bench on this host."""
        from pilot.config import PostgresConfig
        from pilot.managers.platform import is_macos

        port = PostgresConfig().port
        if is_macos():
            return port
        while self._port_is_live(port):
            port += 1
        return port

    def _sibling_postgres_password(self) -> str:
        """Return the shared PostgreSQL password from a sibling bench."""
        for _, config in iter_sibling_benches(self.target_directory):
            if config.db_type == "postgres" and config.postgres.root_password:
                return config.postgres.root_password
        return ""

    def _sibling_jwks_admin(self):
        """Return sibling admin config that trusts a remote JWKS issuer."""
        for _, config in iter_sibling_benches(self.target_directory):
            if getattr(config.admin, "jwks_url", ""):
                return config.admin
        return None

    def _sibling_admin_tls(self) -> bool:
        """Return the server-wide admin TLS choice from a sibling bench."""
        for _, config in iter_sibling_benches(self.target_directory):
            return bool(getattr(config.admin, "tls", False))
        return False

    def _pick_port_offset(self, bench_path: Path) -> int:
        """Pick the first base-port offset unused by configs or live processes."""
        from pilot.config.bench_toml_builder import default_ports

        bases = default_ports()
        base_http_port = bases["http_port"]
        used = set()

        for _, config in iter_sibling_benches(bench_path):
            try:
                used.add(config.http_port - base_http_port)
            except Exception:
                continue

        admin_internal_port = bases["admin.port"] + 1

        offset = 0
        while (
            offset in used
            or any(self._port_is_live(base + offset) for base in bases.values())
            or self._port_is_live(admin_internal_port + offset)
        ):
            offset += 1
        return offset

    @staticmethod
    def _port_is_live(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            return False
