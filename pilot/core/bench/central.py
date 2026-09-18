from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.utils import iter_sibling_benches

if TYPE_CHECKING:
    from pilot.core.bench import Bench
    from pilot.core.site import Site


class CentralSetup:
    """Hand a host to Central: the shared flags, its VM hostname aliases, and the
    units that apply the credential."""

    def __init__(
        self,
        bench: "Bench",
        admin_pattern: str = "",
        site_pattern: str = "",
        redirect: bool = False,
        rebootstrap: bool = False,
    ) -> None:
        self.bench = bench
        self.admin_pattern = admin_pattern.strip().lower()
        self.site_pattern = site_pattern.strip().lower()
        self.redirect = redirect
        self.rebootstrap = rebootstrap

    def run(self, on_progress: Callable[[str], None] = lambda message: None) -> None:
        site = self.check()
        on_progress("Enabling Central management")
        self.enable()
        if self.admin_pattern:
            domain = self.bench.config.admin.domain
            on_progress(f"Aliasing {self.admin_pattern} to {domain}")
            self.bench.hostname_aliases.set("admin", self.admin_pattern, domain, self.redirect)
        if site:
            on_progress(f"Aliasing {self.site_pattern} to {site.config.name}")
            self.bench.hostname_aliases.set("site", self.site_pattern, site.config.name, self.redirect)
        # Central's bootstrap unit is written only while central is enabled, so a host
        # already in production needs its process set rebuilt to grow one.
        self.bench.rebuild_process_set(on_progress)

    def check(self) -> "Site | None":
        """The site to alias, once the host is known to hold a single target for each
        alias. Aliasing what Central did not provision would take over a live vhost."""
        from pilot.internal.validators import validate_hostname_pattern

        if others := sorted(config.name for _, config in iter_sibling_benches(self.bench.path)):
            raise BenchError(
                f"Central manages a host with one bench, but this one also has: {', '.join(others)}. "
                f"Write the [central] settings in common_config.toml by hand instead."
            )
        for pattern, label in ((self.admin_pattern, "Admin pattern"), (self.site_pattern, "Site pattern")):
            if pattern and (error := validate_hostname_pattern(pattern, label)):
                raise BenchError(error)
        if self.admin_pattern and not self.bench.config.admin.domain:
            raise BenchError(
                "This bench has no admin domain to alias. Set one with: pilot set-admin-domain <domain>"
            )
        return self._alias_site()

    def enable(self) -> None:
        from pilot.config.common import CommonConfig

        central = self.bench.config.central
        with CommonConfig.open(self.bench.path.parent) as common:
            common.central.enabled = True
            if self.rebootstrap:
                common.central.bootstrapped = False
            central.enabled, central.bootstrapped = common.central.enabled, common.central.bootstrapped

    def _alias_site(self) -> "Site | None":
        if not self.site_pattern:
            return None
        sites = self.bench.sites()
        if len(sites) != 1:
            listed = ", ".join(site.config.name for site in sites) or "none"
            raise BenchError(
                f"--site-pattern aliases the bench's only site, but it has {len(sites)}: {listed}."
            )
        return sites[0]
