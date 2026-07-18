from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config.site import SiteConfig
from pilot.utils import run_command

if TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.core.bench import Bench


class Site:
    def __init__(self, config: SiteConfig, bench: "Bench") -> None:
        self.config = config
        self.bench = bench

    @classmethod
    def for_name(cls, name: str, bench: "Bench") -> "Site":
        """Look up an existing site by name, with no other config known yet."""
        return cls(SiteConfig(name=name, apps=[]), bench)

    @property
    def path(self) -> Path:
        return self.bench.sites_path / self.config.name

    @property
    def exists(self) -> bool:
        return (self.path / "site_config.json").exists()

    def _frappe_call(self, *args: str) -> list[str]:
        """Build a frappe bench_helper command."""
        return [*self.bench.frappe_call, *args]

    def create(self, db_type: str | None = None) -> None:
        from pilot.core.site_commands import SiteCommands

        SiteCommands(self).create(db_type)

    def restore(
        self, db_file: str, public_files: str | None = None, private_files: str | None = None
    ) -> None:
        from pilot.core.site_commands import SiteCommands

        SiteCommands(self).restore(db_file, public_files, private_files)

    def reinstall(self, admin_password: str) -> None:
        from pilot.core.site_commands import SiteCommands

        SiteCommands(self).reinstall(admin_password)

    def install_app(self, app: "App") -> None:
        from pilot.core.site_apps import SiteApps

        SiteApps(self).install_app(app)

    def install_app_with_dependencies(self, app: "App") -> list["App"]:
        """Install `app` here, then resolve the hooks.py-declared dependencies
        frappe's own install-app cascades onto this site — returned so callers
        can build their assets too, since frappe's cascade doesn't report them
        back."""
        from pilot.core.site_apps import SiteApps

        return SiteApps(self).install_app_with_dependencies(app)

    def uninstall_app(self, app: "App", force: bool = False) -> None:
        from pilot.core.site_apps import SiteApps

        SiteApps(self).uninstall_app(app, force)

    def list_apps(self) -> list[str]:
        from pilot.core.site_apps import SiteApps

        return SiteApps(self).list_apps()

    def installed_apps(self) -> list[str]:
        """Installed app names for this site, using the fastest available
        method (site_config.json's cache, then a direct DB query, then a
        frappe subprocess) — cheaper than list_apps() for read-heavy callers
        like the admin API."""
        from pilot.core.site_apps import SiteApps

        return SiteApps(self).installed_apps()

    def migrate(self, skip_failing: bool = False) -> None:
        from pilot.core.site_commands import SiteCommands

        SiteCommands(self).migrate(skip_failing)

    def uninstall_apps(
        self,
        app_names: list[str],
        force: bool = False,
        on_progress: Callable[[str], None] = lambda message: None,
    ) -> None:
        from pilot.core.site_apps import SiteApps

        SiteApps(self).uninstall_apps(app_names, force, on_progress)

    def _remove_app_if_not_on_any_site(
        self, app_name: str, on_progress: Callable[[str], None]
    ) -> None:
        from pilot.core.site_apps import SiteApps

        SiteApps(self).remove_app_if_not_on_any_site(app_name, on_progress)

    def drop(self, on_progress: Callable[[str], None] = lambda message: None) -> None:
        from pilot.managers.nginx import NginxManager

        provider_domains = self._provider_domains()
        cmd = [*self.bench.frappe_call, "frappe", "drop-site", "--force", self.config.name]
        cmd += self.bench.db_root_args()
        on_progress(f"Dropping site '{self.config.name}'...")
        run_command(cmd, cwd=self.bench.sites_path, stream_output=True)
        self._remove_from_bench_toml()
        self._release_domains(provider_domains)
        on_progress(f"\nSite '{self.config.name}' dropped.")
        NginxManager(self.bench).reload_for_site_change()

    def _provider_domains(self) -> list[str]:
        """Hostnames this site claimed at the provider — its own name (the route a
        wildcard create registers) plus its custom domains — captured before the
        drop removes the site config so nothing is left dangling at the edge."""
        from pilot.core.site_drop import SiteDropper

        return SiteDropper(self).provider_domains()

    def _release_domains(self, domains: list[str]) -> None:
        """Release the captured domains at the provider, only after the drop has
        succeeded. Best effort: a teardown failure leaves a stale route, but the
        site is already gone so it must not turn a successful drop into an error."""
        from pilot.core.site_drop import SiteDropper

        SiteDropper(self).release_domains(domains)

    def _remove_from_bench_toml(self) -> None:
        from pilot.core.site_drop import SiteDropper

        SiteDropper(self).remove_from_bench_toml()

    @classmethod
    def provision(
        cls,
        bench: "Bench",
        name: str,
        apps: list[str],
        admin_password: str,
        db_type: str | None = None,
        on_progress: Callable[[str], None] = lambda message: None,
    ) -> "Site":
        """Create a new site end to end: validate, register, create via
        frappe, install apps, build assets, reload nginx, obtain a cert."""
        from pilot.core.site_provisioning import SiteProvisioner

        return SiteProvisioner(bench, name, apps, admin_password, db_type).provision(on_progress)

    def set_ssl(self, enabled: bool) -> None:
        set_site_ssl_flag(self.bench.sites_path, self.config.name, enabled)

    def _build_missing_assets(self) -> None:
        from pilot.core.site_provisioning import SiteProvisioner

        SiteProvisioner(
            self.bench,
            self.config.name,
            self.config.apps,
            self.config.admin_password,
        ).build_missing_assets()


def _validate_new_site(bench: "Bench", name: str, apps: list[str]) -> bool:
    """Validate a candidate new-site name/app-list; returns whether the name
    was matched via one of the bench's wildcard domains (which then must be
    registered with the domain provider before creation)."""
    from pilot.core.site_provisioning import validate_new_site

    return validate_new_site(bench, name, apps)


def provision_from_backup(
    bench: "Bench",
    name: str,
    db_file: str,
    admin_password: str,
    public_files: str | None = None,
    private_files: str | None = None,
    on_progress: Callable[[str], None] = lambda message: None,
) -> "Site":
    """Create a new site (same engine as the backup) and restore into it."""
    from pilot.core.site_provisioning import provision_from_backup as _provision_from_backup

    return _provision_from_backup(
        bench,
        name,
        db_file,
        admin_password,
        public_files,
        private_files,
        on_progress,
    )


def _should_enable_ssl(bench: "Bench", name: str) -> bool:
    from pilot.core.site_provisioning import should_enable_ssl

    return should_enable_ssl(bench, name)


def _register_with_provider(bench: "Bench", name: str) -> None:
    """A wildcard-derived name is the provider's to allocate; provision it before
    creating the site so a provider failure leaves no orphan site."""
    from pilot.core.site_provisioning import register_with_provider

    register_with_provider(bench, name)


def list_installed_apps(site_config: dict, bench_root: Path, site_name: str) -> list[str]:
    """Return installed app names for a site, using the fastest available method."""
    from pilot.core.site_config import list_installed_apps as _list_installed_apps

    return _list_installed_apps(site_config, bench_root, site_name)


def query_installed_apps_via_db(site_config: dict) -> list[str] | None:
    from pilot.core.site_config import query_installed_apps_via_db as _query_installed_apps_via_db

    return _query_installed_apps_via_db(site_config)


def set_site_ssl_flag(sites_root: Path, site_name: str, enabled: bool) -> None:
    """Flip a site's `ssl` flag in its site_config.json, guarding against a
    site name that would escape `sites_root` via a symlink or `..`. A plain
    function (not a Site method) so it works from contexts — like a task
    callback — that only have a bench_root and site name, not a live Bench."""
    from pilot.core.site_config import set_site_ssl_flag as _set_site_ssl_flag

    _set_site_ssl_flag(sites_root, site_name, enabled)


def _safe_site_config_path(sites_root: Path, site_name: str) -> Path:
    from pilot.core.site_config import safe_site_config_path

    return safe_site_config_path(sites_root, site_name)


def _query_installed_apps_via_frappe(bench_root: Path, site_name: str) -> list[str]:
    from pilot.core.site_config import query_installed_apps_via_frappe

    return query_installed_apps_via_frappe(bench_root, site_name)
