from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config import SiteConfig
from pilot.exceptions import BenchError, CronError
from pilot.internal.command import run_command
from pilot.managers.cron import CronManager

if TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.core.bench import Bench
    from pilot.core.site.backups import SiteBackups
    from pilot.core.site.config.database_state import SiteDatabaseState
    from pilot.core.site.config.file import SiteConfigFile
    from pilot.core.site.console.completions import ConsoleCompletions
    from pilot.core.site.console.script import SiteScript
    from pilot.core.site.domains import SiteDomains
    from pilot.core.site.frappe_cloud import SiteFrappeCloud
    from pilot.core.site.migration_backup import SiteMigrationBackup
    from pilot.core.site.restore import BackupRun


class Site:
    def __init__(self, config: SiteConfig, bench: "Bench") -> None:
        self.config = config
        self.bench = bench

    @property
    def path(self) -> Path:
        return self.bench.sites_path / self.config.name

    def clone(
        self,
        name: str,
        destination: "Bench | None" = None,
        admin_password: str | None = None,
        on_progress=print,
    ) -> "Site":
        from pilot.core.site.clone import SiteClone

        password = secrets.token_urlsafe(24) if admin_password is None else admin_password
        return SiteClone(self, destination or self.bench, name, password).run(on_progress)

    @property
    def exists(self) -> bool:
        return (self.path / "site_config.json").exists()

    @cached_property
    def backups(self) -> "SiteBackups":
        from pilot.core.site.backups import SiteBackups

        return SiteBackups(self)

    @cached_property
    def console_completions(self) -> "ConsoleCompletions":
        from pilot.core.site.console.completions import ConsoleCompletions

        return ConsoleCompletions(self)

    @cached_property
    def domains(self) -> "SiteDomains":
        from pilot.core.site.domains import SiteDomains

        return SiteDomains(self)

    @cached_property
    def frappe_cloud(self) -> "SiteFrappeCloud":
        from pilot.core.site.frappe_cloud import SiteFrappeCloud

        return SiteFrappeCloud(self)

    @cached_property
    def migration_backup(self) -> "SiteMigrationBackup":
        from pilot.core.site.migration_backup import SiteMigrationBackup

        return SiteMigrationBackup(self)

    @property
    def config_file(self) -> "SiteConfigFile":
        from pilot.core.site.config.file import SiteConfigFile

        return SiteConfigFile(self.bench.sites_path, self.config.name)

    @property
    def database_state(self) -> "SiteDatabaseState":
        from pilot.core.site.config.database_state import SiteDatabaseState

        return SiteDatabaseState(self.bench.path, self.config.name)

    @property
    def db_type(self) -> str:
        """The engine frappe connects this site to, with frappe's own fallback."""
        return self.config_file.read().get("db_type") or "mariadb"

    @property
    def maintenance_mode(self) -> bool:
        return bool(self.maintenance_settings["maintenance_mode"])

    @property
    def maintenance_settings(self) -> dict[str, int]:
        config = self.config_file.read()
        return {
            "maintenance_mode": int(bool(config.get("maintenance_mode"))),
            "pause_scheduler": int(bool(config.get("pause_scheduler"))),
        }

    def set_maintenance_mode(self, enabled: bool) -> None:
        value = 1 if enabled else 0
        self.set_maintenance_settings({"maintenance_mode": value, "pause_scheduler": value})

    def set_maintenance_settings(self, settings: dict[str, int]) -> None:
        self.config_file.set_values(settings)

    @contextmanager
    def under_maintenance(self) -> Iterator[None]:
        """Temporarily enable maintenance mode and restore the prior state."""
        original = self.maintenance_settings
        self.set_maintenance_mode(True)
        try:
            yield
        finally:
            self.set_maintenance_settings(original)

    def ensure_exists(self) -> None:
        if not self.exists:
            raise BenchError(f"Site '{self.config.name}' does not exist in this bench.")

    @contextmanager
    def reserve(self) -> Iterator[None]:
        """Refuse task submissions for this site until the block exits."""
        from pilot.internal.tasks.store import TaskStore

        with TaskStore(self.bench.path).reserve(f"site:{self.config.name.lower()}"):
            yield

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

    def restore_backup(
        self,
        run: BackupRun,
        parts: list[str],
        on_progress: Callable[[str], None] = print,
        skip_failing_patches: bool = False,
    ) -> None:
        """Restore `parts` of a backup run in maintenance mode, then migrate."""
        from pilot.core.site.restore import SiteRestore

        SiteRestore(self).restore(run, parts, on_progress, skip_failing_patches)
        # The restored database decides which apps the site runs.
        if "database" in parts:
            self.bench.processes.apply()

    def reinstall(self, admin_password: str) -> None:
        from pilot.core.site.commands import SiteCommands

        SiteCommands(self).reinstall(admin_password)

    def install_app(self, app: "App", force: bool = False) -> None:
        from pilot.core.site.apps import SiteApps

        apps = SiteApps(self)
        apps.install_app(app, force=force)
        # Frappe installs the required apps along with it.
        if self.bench.processes.has_declarations(apps.get_dependency_closure(app)):
            self.bench.processes.apply()

    def install_app_with_dependencies(self, app: "App") -> list["App"]:
        """Install the app and return dependencies cascaded by Frappe."""
        from pilot.core.site.apps import SiteApps

        return SiteApps(self).install_app_with_dependencies(app)

    def uninstall_app(self, app: "App", force: bool = False) -> None:
        from pilot.core.site.apps import SiteApps

        # Checked first: the app may leave the bench along with its declarations.
        has_processes = self.bench.processes.has_declarations([app.config.name])
        SiteApps(self).uninstall_app(app, force)
        if has_processes:
            self.bench.processes.apply()

    def enable_app(self, app: "App") -> None:
        from pilot.core.site.apps import SiteApps

        apps = SiteApps(self)
        apps.enable_app(app)
        # The required apps come back with it.
        if self.bench.processes.has_declarations(apps.get_dependency_closure(app)):
            self.bench.processes.apply()

    def disable_app(self, app: "App") -> None:
        from pilot.core.site.apps import SiteApps

        SiteApps(self).disable_app(app)
        if self.bench.processes.has_declarations([app.config.name]):
            self.bench.processes.apply()

    def disabled_apps(self) -> list[str]:
        from pilot.core.site.apps import SiteApps

        return SiteApps(self).disabled_apps()

    @property
    def unfinished_installs(self) -> list[str]:
        """Apps listed as installed whose install failed after Frappe listed them."""
        from pilot.core.site.apps import SiteApps

        return SiteApps(self).get_unfinished_installs()

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

    @property
    def has_pending_background_jobs(self) -> bool:
        from pilot.core.site.commands import SiteCommands

        return SiteCommands(self).has_pending_background_jobs()

    def script(self, code: str, commit: bool = False) -> "SiteScript":
        """A Python script for this site; iterate `run()` to execute it."""
        from pilot.core.site.console.script import SiteScript

        return SiteScript(self, code, commit)

    def clear_cache(self) -> None:
        from pilot.core.site.commands import SiteCommands

        SiteCommands(self).clear_cache()

    def enable_scheduler(self) -> None:
        from pilot.core.site.commands import SiteCommands

        SiteCommands(self).enable_scheduler()

    def build_assets(self) -> None:
        """Rebuild the assets of the apps this site runs. Assets are shared by every
        site on the bench that has those apps."""
        bench_apps = {app.config.name for app in self.bench.apps()}
        self.bench.rebuild_assets(
            apps=[name for name in self.active_apps() if name in bench_apps], force=True
        )

    def uninstall_apps(
        self,
        app_names: list[str],
        force: bool = False,
        on_progress: Callable[[str], None] = lambda message: None,
        remove_from_bench: bool = True,
    ) -> None:
        from pilot.core.site.apps import SiteApps

        SiteApps(self).uninstall_apps(app_names, force, on_progress, remove_from_bench)

    def drop(
        self,
        on_progress: Callable[[str], None] = lambda message: None,
        no_backup: bool = False,
        force: bool = False,
        archived_sites_path: str | None = None,
    ) -> None:
        from pilot.core.site.commands import SiteCommands
        from pilot.managers.nginx import NginxManager

        self.ensure_exists()
        provider_domains = self._provider_domains()
        has_processes = self.bench.processes.has_declarations(self.active_apps())
        cmd = [*self.bench.frappe_call, "frappe", "drop-site", self.config.name]
        if no_backup:
            cmd.append("--no-backup")
        # Frappe's --force drops the database even when the backup before it failed.
        if force or no_backup:
            cmd.append("--force")
        if archived_sites_path:
            cmd += ["--archived-sites-path", archived_sites_path]
        on_progress(f"Dropping site '{self.config.name}'...")
        with SiteCommands(self).setup_credentials(self.db_type) as credentials:
            run_command(cmd + credentials, cwd=self.bench.sites_path, stream_output=True)
        # The site directory is archived, so only the cron entry remains.
        try:
            CronManager(self.bench.path).remove_schedule(self.config.name)
        except CronError as error:
            on_progress(f"Could not remove the site's backup schedule: {error}")
        self._remove_from_bench_toml()
        self._release_domains(provider_domains)
        on_progress(f"\nSite '{self.config.name}' dropped.")
        NginxManager(self.bench).reload_for_site_change()
        if has_processes:
            self.bench.processes.apply()

    def rename_to(
        self,
        new_name: str,
        on_progress: Callable[[str], None] = lambda message: None,
        keep_old_hostname: bool = True,
        make_primary: bool = False,
    ) -> None:
        from pilot.core.site.rename import SiteRename

        SiteRename(self, new_name, keep_old_hostname, make_primary).run(on_progress)

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
        *,
        schedule_backups: bool = True,
        skip_assets: bool = False,
    ) -> "Site":
        """Create a site, install apps, reload nginx, and issue TLS if needed."""
        from pilot.core.site.provisioning import SiteProvisioner

        return SiteProvisioner(bench, name, apps, admin_password, db_type, skip_assets).provision(
            on_progress, schedule_backups=schedule_backups
        )

    def finish_provisioning(
        self, apps: list[str], on_progress: Callable[[str], None] = lambda message: None
    ) -> None:
        """Install `apps` and run the provisioning steps after them, for a site whose
        provisioning stopped before its apps were installed."""
        from pilot.core.site.provisioning import SiteProvisioner

        SiteProvisioner(self.bench, self.config.name, apps, None).finish(self, on_progress)

    def set_ssl(self, enabled: bool) -> None:
        self.config_file.set_values({"ssl": enabled})

    def admin_login_url(
        self, proxy_tls: bool = False, user: str = "Administrator", full_name: str = ""
    ) -> str | None:
        from pilot.core.site.login import SiteLogin

        return SiteLogin(self).admin_url(proxy_tls=proxy_tls, user=user, full_name=full_name)
