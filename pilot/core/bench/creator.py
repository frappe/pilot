from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.core.bench.ports import pick_port_offset
from pilot.exceptions import BenchAlreadyExistsError

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class BenchCreator:
    """Creates a bench. Host-shared settings (MariaDB/Postgres server, ACME
    email, admin JWKS trust) live in common_config.toml. Database credentials
    are generated at `pilot init`, not here - see BenchInitializer."""

    def __init__(
        self,
        target_directory: Path,
        name: str,
        process_manager: str = "",
        admin_domain: str = "",
        admin_tls: bool | None = None,
        db_type: str = "mariadb",
        admin_password: str = "",
    ) -> None:
        self.target_directory = target_directory
        self.name = name
        self.process_manager = process_manager
        self.admin_domain = admin_domain
        self.admin_tls = admin_tls
        self.db_type = db_type
        self.admin_password = admin_password

    def run(self, on_progress: Callable[[str], None] = lambda message: None) -> "Bench":
        from pilot.config import BenchConfig
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

        offset = pick_port_offset(benches_dir)
        on_progress("Writing bench.toml")
        settings = self._initial_settings()

        BenchConfig.write_flat(bench_toml, self.name, settings, port_offset=offset)

        admin_port = BenchConfig.default_ports()["admin.port"] + offset
        on_progress(f"\nBench '{self.name}' created at {self.target_directory}")
        if not self.admin_password:
            on_progress(f"\nGenerated admin password: {settings['admin_password']}")
            on_progress("  Change it any time with 'pilot set-admin-password'.")
        on_progress("\nNext step:")
        on_progress("  pilot start")
        on_progress(f"  Then open the sign-in link it prints for http://localhost:{admin_port}.")

        return Bench(self.target_directory)

    def _initial_settings(self) -> dict:
        import secrets

        settings = {
            "admin_enabled": True,
            "admin_password": self.admin_password or secrets.token_urlsafe(12),
            "admin_domain": self.admin_domain,
            # admin.tls is a per-bench choice, not inherited from siblings.
            "admin_tls": bool(self.admin_tls),
            "db_type": self.db_type,
            "lite_mode_enabled": True,
        }
        if self.process_manager:
            settings["production_process_manager"] = self.process_manager
        return settings
