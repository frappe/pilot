from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.utils import write_private_text

if TYPE_CHECKING:
    from pilot.core.site import Site


class SiteRename:
    def __init__(self, site: "Site", new_name: str) -> None:
        self.site = site
        self.bench = site.bench
        self.old_name = site.config.name
        self.new_name = new_name

    def run(self, on_progress: Callable[[str], None]) -> None:
        self.validate()
        ssl_enabled = self.site.config.ssl

        on_progress(f"Renaming site '{self.old_name}' -> '{self.new_name}'...")
        self.site.path.rename(self.bench.sites_path / self.new_name)

        self._update_default_site()
        self._rename_in_bench_toml()
        self._add_to_hosts()
        self._reload_nginx()

        on_progress(f"\nSite renamed to '{self.new_name}'.")
        self.run_followups(ssl_enabled, on_progress)

    def validate(self) -> None:
        from pilot.utils import host_owner, normalize_host

        if self.new_name == self.old_name:
            raise BenchError("New name is the same as the current name.")

        sites = {site.config.name: site for site in self.bench.sites()}
        if self.old_name not in sites:
            raise BenchError(f"Site '{self.old_name}' does not exist in this bench.")

        if self.new_name in sites or (self.bench.sites_path / self.new_name).exists():
            raise BenchError(f"Site '{self.new_name}' already exists in this bench.")

        owner = host_owner(self.bench.path, self.new_name)
        if owner:
            raise BenchError(
                f"'{self.new_name}' is already used by bench '{owner}' (as a site or its admin domain). "
                f"All benches share one nginx, so hostnames must be unique."
            )
        if normalize_host(self.new_name) == normalize_host(self.bench.config.admin.domain):
            raise BenchError(
                f"Site '{self.new_name}' clashes with this bench's admin domain. "
                f"An admin domain must not match a site domain."
            )

    def run_followups(self, ssl_enabled: bool, on_progress: Callable[[str], None]) -> None:
        name = self.bench.config.name
        if self.bench.config.production.enabled:
            self._run_or_advise(
                "production setup",
                lambda: self.bench.setup_production(on_progress=on_progress),
                f"bench setup production -b {name}",
                on_progress,
            )
        elif ssl_enabled:
            self._run_or_advise(
                "Let's Encrypt setup",
                self.bench.setup_letsencrypt,
                f"bench setup letsencrypt -b {name}",
                on_progress,
            )

    def _update_default_site(self) -> None:
        path = self.bench.sites_path / "common_site_config.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        if data.get("default_site") == self.old_name:
            data["default_site"] = self.new_name
            write_private_text(path, json.dumps(data, indent=2) + "\n")

    def _rename_in_bench_toml(self) -> None:
        from pilot.config import BenchConfig

        with BenchConfig.open(self.bench.path, mode="raw") as raw:
            for site in raw.get("sites", []):
                if site.get("name") == self.old_name:
                    site["name"] = self.new_name

    def _add_to_hosts(self) -> None:
        if self.bench.config.production.process_manager != "none":
            return
        from pilot.managers.platform import add_hosts_entry

        add_hosts_entry(self.new_name)

    def _reload_nginx(self) -> None:
        from pilot.managers.nginx import NginxManager

        NginxManager(self.bench).reload_for_site_change()

    def _run_or_advise(
        self,
        label: str,
        fn,
        manual_cmd: str,
        on_progress: Callable[[str], None],
    ) -> None:
        on_progress(f"\nRunning {label} for the new domain...")
        try:
            fn()
        except (Exception, SystemExit) as exc:
            detail = f" ({exc})" if str(exc) else ""
            on_progress(f"\n{label} did not complete{detail}. Run it yourself once resolved:\n  {manual_cmd}")
