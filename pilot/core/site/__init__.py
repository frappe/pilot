from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config import SiteConfig
from pilot.utils import run_command

if TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.core.bench import Bench
    from pilot.core.site.backups import SiteBackups
    from pilot.core.site.domains import SiteDomains
    from pilot.core.site.migration_backup import SiteMigrationBackup


class Site:
    def __init__(self, config: SiteConfig, bench: "Bench") -> None:
        self.config = config
        self.bench = bench

    @property
    def path(self) -> Path:
        return self.bench.sites_path / self.config.name

    @property
    def exists(self) -> bool:
        return (self.path / "site_config.json").exists()

    @cached_property
    def backups(self) -> "SiteBackups":
        from pilot.core.site.backups import SiteBackups

        return SiteBackups(self)

    @cached_property
    def domains(self) -> "SiteDomains":
        from pilot.core.site.domains import SiteDomains

        return SiteDomains(self)

    @cached_property
    def migration_backup(self) -> "SiteMigrationBackup":
        from pilot.core.site.migration_backup import SiteMigrationBackup

        return SiteMigrationBackup(self)

    @property
    def maintenance_mode(self) -> bool:
        return bool(self.maintenance_settings["maintenance_mode"])

    @property
    def maintenance_settings(self) -> dict[str, int]:
        from pilot.core.site.config import read_site_config

        config = read_site_config(self.path)
        return {
            "maintenance_mode": int(bool(config.get("maintenance_mode"))),
            "pause_scheduler": int(bool(config.get("pause_scheduler"))),
        }

    def set_maintenance_mode(self, enabled: bool) -> None:
        value = 1 if enabled else 0
        self.set_maintenance_settings({"maintenance_mode": value, "pause_scheduler": value})

    def set_maintenance_settings(self, settings: dict[str, int]) -> None:
        import json

        from pilot.core.site.config import safe_site_config_path
        from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked

        config_path = safe_site_config_path(self.bench.sites_path, self.config.name)
        with exclusive_file_lock(config_path):
            config = json.loads(config_path.read_text())
            config.update(settings)
            replace_private_text_locked(config_path, json.dumps(config, indent=1))

    @contextmanager
    def under_maintenance(self) -> Iterator[None]:
        """Temporarily enable maintenance mode and restore the prior state."""
        original = self.maintenance_settings
        self.set_maintenance_mode(True)
        try:
            yield
        finally:
            self.set_maintenance_settings(original)

    def _frappe_call(self, *args: str) -> list[str]:
        """Build a frappe bench_helper command."""
        return [*self.bench.frappe_call, *args]

    def create(self, db_type: str | None = None) -> None:
        from pilot.core.site.commands import SiteCommands

        SiteCommands(self).create(db_type)

    def restore(
        self, db_file: str, public_files: str | None = None, private_files: str | None = None
    ) -> None:
        from pilot.core.site.commands import SiteCommands

        SiteCommands(self).restore(db_file, public_files, private_files)

    def reinstall(self, admin_password: str) -> None:
        from pilot.core.site.commands import SiteCommands

        SiteCommands(self).reinstall(admin_password)

    def install_app(self, app: "App") -> None:
        from pilot.core.site.apps import SiteApps

        SiteApps(self).install_app(app)

    def install_app_with_dependencies(self, app: "App") -> list["App"]:
        """Install the app and return dependencies cascaded by Frappe."""
        from pilot.core.site.apps import SiteApps

        return SiteApps(self).install_app_with_dependencies(app)

    def uninstall_app(self, app: "App", force: bool = False) -> None:
        from pilot.core.site.apps import SiteApps

        SiteApps(self).uninstall_app(app, force)

    def enable_app(self, app: "App") -> None:
        from pilot.core.site.apps import SiteApps

        SiteApps(self).enable_app(app)

    def disable_app(self, app: "App") -> None:
        from pilot.core.site.apps import SiteApps

        SiteApps(self).disable_app(app)

    def disabled_apps(self) -> list[str]:
        from pilot.core.site.apps import SiteApps

        return SiteApps(self).disabled_apps()

    def get_missing_dependencies(self, app: "App") -> list[str]:
        """Apps `app` requires that this site does not have at all."""
        from pilot.core.site.apps import SiteApps

        return SiteApps(self).get_missing_dependencies(app)

    def list_apps(self) -> list[str]:
        """Every app the site holds data for, disabled ones included."""
        from pilot.core.site.apps import SiteApps

        return SiteApps(self).list_apps()

    def installed_apps(self) -> list[str]:
        """Every app installed on the site, disabled ones included. Read from cache,
        DB, then Frappe as fallback."""
        from pilot.core.site.apps import SiteApps

        return SiteApps(self).installed_apps()

    def active_apps(self) -> list[str]:
        """Installed apps the site actually runs - what the Admin UI shows as installed."""
        from pilot.core.site.apps import SiteApps

        return SiteApps(self).active_apps()

    def migrate(self, skip_failing: bool = False) -> str:
        """Run migration through the shared path, returning the full captured output."""
        from pilot.core.site.commands import SiteCommands

        return SiteCommands(self).migrate(skip_failing)

    def clear_cache(self) -> None:
        from pilot.core.site.commands import SiteCommands

        SiteCommands(self).clear_cache()

    def uninstall_apps(
        self,
        app_names: list[str],
        force: bool = False,
        on_progress: Callable[[str], None] = lambda message: None,
    ) -> None:
        from pilot.core.site.apps import SiteApps

        SiteApps(self).uninstall_apps(app_names, force, on_progress)

    def _remove_app_if_not_on_any_site(self, app_name: str, on_progress: Callable[[str], None]) -> None:
        from pilot.core.site.apps import SiteApps

        SiteApps(self).remove_app_if_not_on_any_site(app_name, on_progress)

    def drop(self, on_progress: Callable[[str], None] = lambda message: None) -> None:
        from pilot.core.site.commands import SiteCommands
        from pilot.managers.nginx import NginxManager

        provider_domains = self._provider_domains()
        cmd = [*self.bench.frappe_call, "frappe", "drop-site", "--force", self.config.name]
        on_progress(f"Dropping site '{self.config.name}'...")
        with SiteCommands(self).setup_credentials(self.bench.config.db_type) as credentials:
            run_command(cmd + credentials, cwd=self.bench.sites_path, stream_output=True)
        self._remove_from_bench_toml()
        self._release_domains(provider_domains)
        on_progress(f"\nSite '{self.config.name}' dropped.")
        NginxManager(self.bench).reload_for_site_change()

    def rename_to(
        self,
        new_name: str,
        on_progress: Callable[[str], None] = lambda message: None,
        keep_old_hostname: bool = True,
    ) -> None:
        from pilot.core.site.rename import SiteRename

        SiteRename(self, new_name, keep_old_hostname).run(on_progress)

    def _provider_domains(self) -> list[str]:
        """Capture provider-owned domains before the site config is removed."""
        from pilot.core.site.drop import SiteDropper

        return SiteDropper(self).provider_domains()

    def _release_domains(self, domains: list[str]) -> None:
        """Best-effort provider cleanup after a successful local drop."""
        from pilot.core.site.drop import SiteDropper

        SiteDropper(self).release_domains(domains)

    def _remove_from_bench_toml(self) -> None:
        from pilot.core.site.drop import SiteDropper

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
        """Create a site, install apps, reload nginx, and issue TLS if needed."""
        from pilot.core.site.provisioning import SiteProvisioner

        return SiteProvisioner(bench, name, apps, admin_password, db_type).provision(on_progress)

    def set_ssl(self, enabled: bool) -> None:
        set_site_ssl_flag(self.bench.sites_path, self.config.name, enabled)

    def clear_certificate_pin(self) -> None:
        from pilot.core.site.config import clear_certificate_pin

        clear_certificate_pin(self.bench.sites_path, self.config.name)

    def public_config(self) -> dict:
        from pilot.core.site.config import read_public_config

        return read_public_config(self.path)

    def update_public_config(self, patch: dict) -> dict:
        from pilot.core.site.config import update_public_config

        return update_public_config(self.path, patch)

    def admin_login_url(self, proxy_tls: bool = False) -> str | None:
        from pilot.core.site.login import SiteLogin

        return SiteLogin(self).admin_url(proxy_tls=proxy_tls)

    def _build_missing_assets(self) -> None:
        from pilot.core.site.provisioning import SiteProvisioner

        SiteProvisioner(
            self.bench,
            self.config.name,
            self.config.apps,
            self.config.admin_password,
        ).build_missing_assets()


def _validate_new_site(bench: "Bench", name: str, apps: list[str]) -> bool:
    """Validate the site name/apps and return whether a wildcard matched."""
    from pilot.core.site.provisioning import validate_new_site

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
    from pilot.core.site.provisioning import provision_from_backup as _provision_from_backup

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
    from pilot.core.site.provisioning import should_enable_ssl

    return should_enable_ssl(bench, name)


def _register_with_provider(bench: "Bench", name: str) -> None:
    """Reserve a wildcard-derived name before creating the local site."""
    from pilot.core.site.provisioning import register_with_provider

    register_with_provider(bench, name)


def list_installed_apps(site_config: dict, bench_root: Path, site_name: str) -> list[str]:
    """Return installed app names for a site, using the fastest available method."""
    from pilot.core.site.config import list_installed_apps as _list_installed_apps

    return _list_installed_apps(site_config, bench_root, site_name)


def list_active_apps(site_config: dict, bench_root: Path, site_name: str) -> list[str]:
    """Installed apps the site actually runs, disabled ones dropped."""
    from pilot.core.site.config import list_active_apps as _list_active_apps

    return _list_active_apps(site_config, bench_root, site_name)


def query_installed_apps_via_db(bench_root: Path, site_name: str) -> list[str] | None:
    from pilot.core.site.config import query_installed_apps_via_db as _query_installed_apps_via_db

    return _query_installed_apps_via_db(bench_root, site_name)


def exclude_disabled_apps(apps: list[str], bench_root: Path, site_name: str) -> list[str]:
    """Drop apps the site holds disabled - Pilot reports those as uninstalled."""
    from pilot.core.site.config import exclude_disabled_apps as _exclude_disabled_apps

    return _exclude_disabled_apps(apps, bench_root, site_name)


def is_setup_complete(bench_root: Path, site_name: str) -> bool | None:
    from pilot.core.site.config import is_setup_complete as _is_setup_complete

    return _is_setup_complete(bench_root, site_name)


def set_site_ssl_flag(sites_root: Path, site_name: str, enabled: bool) -> None:
    """Flip a site's ssl flag after resolving the path safely."""
    from pilot.core.site.config import set_site_ssl_flag as _set_site_ssl_flag

    _set_site_ssl_flag(sites_root, site_name, enabled)


def _safe_site_config_path(sites_root: Path, site_name: str) -> Path:
    from pilot.core.site.config import safe_site_config_path

    return safe_site_config_path(sites_root, site_name)


def _query_installed_apps_via_frappe(bench_root: Path, site_name: str) -> list[str]:
    from pilot.core.site.config import query_installed_apps_via_frappe

    return query_installed_apps_via_frappe(bench_root, site_name)
