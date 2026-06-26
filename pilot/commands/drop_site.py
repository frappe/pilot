from __future__ import annotations

import sys
import tomllib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class DropSiteCommand:
    def __init__(self, bench: "Bench", name: str) -> None:
        self.bench = bench
        self.name = name

    def run(self) -> None:
        from pilot.utils import run_command

        provider_domains = self._provider_domains()
        cmd = [*self.bench.frappe_call, "frappe", "drop-site", "--force", self.name]
        if self.bench.config.mariadb.root_password:
            cmd += ["--db-root-password", self.bench.config.mariadb.root_password]
        print(f"Dropping site '{self.name}'...")
        sys.stdout.flush()
        run_command(cmd, cwd=self.bench.sites_path, stream_output=True)
        self._remove_from_bench_toml()
        self._release_domains(provider_domains)
        print(f"\nSite '{self.name}' dropped.")
        self._reload_nginx()

    def _provider_domains(self) -> list[str]:
        """Hostnames this site claimed at the provider — its own name (the route a
        wildcard create registers) plus its custom domains — captured before the
        drop removes the site config so nothing is left dangling at the edge."""
        from pilot.core.domain_controller import DomainRouteProvider

        if not (self.bench.sites_path / self.name / "site_config.json").exists():
            return []
        return [self.name, *DomainRouteProvider(self.bench).domains(self.name)]

    def _release_domains(self, domains: list[str]) -> None:
        """Release the captured domains at the provider, only after the drop has
        succeeded. Best effort: a teardown failure leaves a stale route, but the
        site is already gone so it must not turn a successful drop into an error."""
        if not domains:
            return
        from pilot.core.domain_controller import DomainRouteProvider

        routes = DomainRouteProvider(self.bench)
        for domain in domains:
            routes.release(domain)

    def _remove_from_bench_toml(self) -> None:
        from pilot.utils import write_toml

        bench_toml = self.bench.path / "bench.toml"
        with bench_toml.open("rb") as fh:
            raw = tomllib.load(fh)
        raw["sites"] = [s for s in raw.get("sites", []) if s.get("name") != self.name]
        write_toml(bench_toml, raw)

    def _reload_nginx(self) -> None:
        if not self.bench.config.production.enabled:
            return
        from pilot.managers.nginx_manager import NginxManager
        mgr = NginxManager(self.bench)
        if not mgr.is_installed():
            return
        print("Updating nginx configuration...")
        sys.stdout.flush()
        mgr.generate_config(ssl_ready=True)
        mgr.reload()
