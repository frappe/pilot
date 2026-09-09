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
        self._move_backup_schedule(on_progress)
        self._reissue_site_token(on_progress)
        self._move_provider_route(on_progress)
        self._add_to_hosts()
        self._reload_nginx()

        on_progress(f"\nSite renamed to '{self.new_name}'.")
        self.run_followups(ssl_enabled, on_progress)

    def validate(self) -> None:
        from pilot.utils import host_owner, matches_wildcard, normalize_host

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

        from pilot.core.adapters.domain_provider import DomainRouteProvider

        patterns = DomainRouteProvider.wildcard_domains()
        if patterns and not matches_wildcard(self.new_name, patterns):
            raise BenchError(
                f"Site name must match one of this bench's wildcard domains: {', '.join(patterns)}."
            )

    def run_followups(self, ssl_enabled: bool, on_progress: Callable[[str], None]) -> None:
        name = self.bench.config.name
        if self.bench.config.production.enabled:
            self._run_or_advise(
                "production setup",
                lambda: self.bench.setup_production(on_progress=on_progress),
                f"pilot setup production -b {name}",
                on_progress,
            )
        elif ssl_enabled:
            self._run_or_advise(
                "Let's Encrypt setup",
                self.bench.setup_letsencrypt,
                f"pilot setup letsencrypt -b {name}",
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

    def _move_backup_schedule(self, on_progress: Callable[[str], None]) -> None:
        """The backup cron entry embeds the site name in its marker and command."""
        from pilot.managers.cron import CronManager

        manager = CronManager(self.bench.path)
        schedule = manager.get_schedule(self.old_name)
        if not schedule:
            return
        # Install the new entry before removing the old one, so a failure midway
        # leaves a working schedule instead of none. A crontab failure must not
        # abort a rename that has already moved the site - advise instead.
        backups = self.bench.site(self.new_name).backups
        try:
            manager.set_schedule(self.new_name, schedule, backups._cron_command())
        except Exception as exc:
            on_progress(
                f"\nThe backup schedule could not be moved ({exc}). The old entry for "
                f"'{self.old_name}' is still installed - remove it with 'crontab -e', "
                f"then recreate the schedule from the site's Backups tab."
            )
            return
        try:
            manager.remove_schedule(self.old_name)
        except Exception as exc:
            on_progress(
                f"\nThe new backup schedule is installed, but the old entry for "
                f"'{self.old_name}' could not be removed ({exc}). Remove it with 'crontab -e'."
            )

    def _reissue_site_token(self, on_progress: Callable[[str], None]) -> None:
        """The site-to-bench token embeds the site name; the old one no longer
        authorizes. A token failure must not abort a rename that has already
        moved the site - advise instead."""
        from admin.backend.internal.session import Session
        from pilot.utils import admin_url

        config_path = self.bench.sites_path / self.new_name / "site_config.json"
        try:
            config = json.loads(config_path.read_text())
            if "pilot_auth_token" not in config:
                return
            config["pilot_endpoint"] = admin_url(self.bench.config)
            config["pilot_auth_token"] = Session(self.bench).issue_site_token(
                self.new_name, ttl=365 * 24 * 3600
            )
            # Write beside and rename over: an I/O failure leaves the original
            # site_config.json intact instead of truncated.
            staged = config_path.with_suffix(".json.tmp")
            write_private_text(staged, json.dumps(config, indent=1))
            staged.replace(config_path)
        except Exception as exc:
            on_progress(
                f"\nThe site's bench token could not be reissued ({exc}); the old "
                f"config is unchanged. Once resolved, issue a token and set it as "
                f"pilot_auth_token in site_config.json:\n"
                f"  pilot issue-site-token {self.new_name} -b {self.bench.config.name}"
            )

    def _move_provider_route(self, on_progress: Callable[[str], None]) -> None:
        """Wildcard-managed routing follows the hostname, mirroring new-site
        provisioning: sites are provider-registered only on wildcard hosting.
        Register first: a rejected registration keeps the old route in place for
        the operator to inspect instead of leaving the site with none."""
        from pilot.core.adapters.domain_provider import DomainRouteProvider

        if not DomainRouteProvider.wildcard_domains():
            return
        routes = DomainRouteProvider(self.bench)
        try:
            routes.register(self.new_name, self.new_name)
        except BenchError as exc:
            on_progress(
                f"\nProvider could not register '{self.new_name}' ({exc}). "
                f"The old route was kept; register it yourself once resolved:\n"
                f"  bench-domain-provider register {self.new_name}"
            )
            return
        routes.release(self.old_name)

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
