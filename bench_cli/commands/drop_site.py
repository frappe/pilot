from __future__ import annotations

import sys
import tomllib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class DropSiteCommand:
    def __init__(self, bench: "Bench", name: str) -> None:
        self.bench = bench
        self.name = name

    def run(self) -> None:
        from bench_cli.utils import run_command

        self._deregister_domains()
        cmd = [*self.bench.frappe_call, "frappe", "drop-site", "--force", self.name]
        if self.bench.config.mariadb.root_password:
            cmd += ["--db-root-password", self.bench.config.mariadb.root_password]
        print(f"Dropping site '{self.name}'...")
        sys.stdout.flush()
        run_command(cmd, cwd=self.bench.sites_path, stream_output=True)
        self._remove_from_bench_toml()
        print(f"\nSite '{self.name}' dropped.")
        self._reload_nginx()

    def _deregister_domains(self) -> None:
        """Release the site's custom domains at the provider before dropping. A
        provider failure halts the drop so domains aren't orphaned at the edge.
        Primary is cleared first so deregister's primary guard doesn't block it."""
        from bench_cli.core.domain_controller import DomainRouteProvider

        if not (self.bench.sites_path / self.name / "site_config.json").exists():
            return
        routes = DomainRouteProvider(self.bench)
        routes.set_primary(self.name, None)
        for domain in routes.domains(self.name):
            routes.deregister(self.name, domain)

    def _remove_from_bench_toml(self) -> None:
        from bench_cli.utils import write_toml

        bench_toml = self.bench.path / "bench.toml"
        with bench_toml.open("rb") as fh:
            raw = tomllib.load(fh)
        raw["sites"] = [s for s in raw.get("sites", []) if s.get("name") != self.name]
        write_toml(bench_toml, raw)

    def _reload_nginx(self) -> None:
        if not self.bench.config.production.enabled:
            return
        from bench_cli.managers.nginx_manager import NginxManager
        mgr = NginxManager(self.bench)
        if not mgr.is_installed():
            return
        print("Updating nginx configuration...")
        sys.stdout.flush()
        mgr.generate_config(ssl_ready=True)
        mgr.reload()
