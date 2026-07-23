from __future__ import annotations

from collections.abc import Callable
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config import BenchConfig
from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.config import S3Config
    from pilot.core.app import App, NewAppOptions, RevisionPin
    from pilot.core.bench.migration.store import MigrationStore
    from pilot.core.database import Database
    from pilot.core.site import Site
    from pilot.tasks import TaskRunner


class Bench:
    def __init__(
        self,
        config_or_path: BenchConfig | str | Path,
        path: str | Path | None = None,
    ) -> None:
        if isinstance(config_or_path, BenchConfig):
            if path is None:
                raise TypeError("Bench(config, path) requires a bench path.")
            config = config_or_path
            bench_path = Path(path)
        else:
            if path is not None:
                raise TypeError("Use Bench(config, path) or Bench(path_or_name).")
            bench_path = self._resolve_path(config_or_path)
            config = BenchConfig.read(bench_path)

        self.config = config
        self.path = bench_path
        self._db: "Database | None" = None

    @staticmethod
    def _resolve_path(path_or_name: str | Path) -> Path:
        path = Path(path_or_name).expanduser()
        if isinstance(path_or_name, Path) or path.is_absolute() or path.parent != Path("."):
            return path

        from pilot.utils import benches_dir

        return benches_dir() / path

    @classmethod
    def create_at(
        cls,
        target_directory: Path,
        name: str,
        process_manager: str = "",
        admin_domain: str = "",
        admin_tls: bool | None = None,
        db_type: str = "mariadb",
        on_progress: Callable[[str], None] = lambda message: None,
    ) -> "Bench":
        from pilot.core.bench.creator import BenchCreator

        return BenchCreator(
            target_directory,
            name,
            process_manager=process_manager,
            admin_domain=admin_domain,
            admin_tls=admin_tls,
            db_type=db_type,
        ).run(on_progress)

    @property
    def db(self) -> "Database":
        if self._db is None:
            from pilot.core.database import make_database

            self._db = make_database(self.config)
        return self._db

    @cached_property
    def tasks(self) -> "TaskRunner":
        from pilot.tasks import TaskRunner

        return TaskRunner(self.path)

    @cached_property
    def migrations(self) -> "MigrationStore":
        from pilot.core.bench.migration.store import MigrationStore

        return MigrationStore(self)

    @property
    def apps_path(self) -> Path:
        return self.path / "apps"

    @property
    def sites_path(self) -> Path:
        return self.path / "sites"

    @property
    def env_path(self) -> Path:
        return self.path / "env"

    @property
    def logs_path(self) -> Path:
        return self.path / "logs"

    @property
    def config_path(self) -> Path:
        return self.path / "config"

    @property
    def pids_path(self) -> Path:
        return self.path / "pids"

    @property
    def python(self) -> Path:
        return self.env_path / "bin" / "python"

    @property
    def frappe_call(self) -> list[str]:
        """Command prefix to invoke frappe's bench helper via the venv Python."""
        return [str(self.python), "-m", "frappe.utils.bench_helper"]

    @property
    def db_root_args(self) -> list[str]:
        from pilot.core.bench.config_files import BenchConfigFiles

        return BenchConfigFiles(self).db_root_args

    @property
    def postgres_root_password(self) -> str:
        from pilot.core.bench.config_files import BenchConfigFiles

        return BenchConfigFiles(self).postgres_root_password

    def app(self, name: str) -> "App":
        from pilot.core.bench.inventory import BenchInventory

        return BenchInventory(self).app(name)

    def new_app(
        self, app_name: str, options: "NewAppOptions | None" = None, on_progress=lambda message: None
    ) -> "App":
        from pilot.core.app import App

        return App.scaffold(self, app_name, options, on_progress=on_progress)

    def apps(self) -> list["App"]:
        from pilot.core.bench.inventory import BenchInventory

        return BenchInventory(self).apps()

    def registered_apps(self) -> list[str]:
        from pilot.core.bench.inventory import BenchInventory

        return BenchInventory(self).registered_apps()

    def is_app_installed(self, name: str) -> bool:
        from pilot.core.bench.inventory import BenchInventory

        return BenchInventory(self).is_app_installed(name)

    def init_apps(self) -> list["App"]:
        from pilot.core.bench.inventory import BenchInventory

        return BenchInventory(self).init_apps()

    def sites(self) -> list["Site"]:
        from pilot.core.bench.inventory import BenchInventory

        return BenchInventory(self).sites()

    def site(self, name: str) -> "Site":
        from pilot.config import SiteConfig
        from pilot.core.site import Site

        return Site(SiteConfig(name=name, apps=[]), self)

    def create_directories(self) -> None:
        for directory in [
            self.apps_path,
            self.sites_path,
            self.sites_path / "assets",
            self.logs_path,
            self.config_path,
            self.pids_path,
        ]:
            directory.mkdir(parents=True, exist_ok=True)

    def write_apps_txt(self) -> None:
        from pilot.core.bench.inventory import BenchInventory

        BenchInventory(self).write_apps_txt()

    def set_maintenance_mode(self, enabled: bool) -> None:
        from pilot.core.bench.config_files import BenchConfigFiles

        BenchConfigFiles(self).set_maintenance_mode(enabled)

    def sync_s3_credentials(self, s3_config: S3Config):
        from pilot.core.bench.config_files import BenchConfigFiles

        BenchConfigFiles(self).sync_s3_credentials(s3_config)

    def write_common_site_config(self) -> None:
        from pilot.core.bench.config_files import BenchConfigFiles

        BenchConfigFiles(self).write_common_site_config()

    def restart(self):
        """Restart bench in case we are running in production"""
        self.restart_processes()

    def start(self, on_progress: Callable[[str], None] = lambda message: None) -> None:
        from pilot.core.bench.runtime import BenchRuntime

        BenchRuntime(self).start(on_progress)

    def stop(self, on_progress: Callable[[str], None] = lambda message: None) -> None:
        from pilot.core.bench.runtime import BenchRuntime

        BenchRuntime(self).stop(on_progress)

    def restart_workload(
        self,
        include_admin: bool = False,
        on_progress: Callable[[str], None] = lambda message: None,
    ) -> None:
        from pilot.core.bench.runtime import BenchRuntime

        BenchRuntime(self).restart_workload(include_admin, on_progress)

    def run_production_action(self, action: str) -> None:
        from pilot.core.bench.runtime import BenchRuntime

        BenchRuntime(self).run_production_action(action)

    def rebuild_runtime_config(self) -> None:
        from pilot.core.bench.runtime import BenchRuntime

        BenchRuntime(self).rebuild_config()

    def rebuild_assets(self, force: bool = False) -> None:
        from pilot.core.bench.runtime import BenchRuntime

        BenchRuntime(self).rebuild_assets(force)

    def install_requirements(self, on_progress: Callable[[str], None] = lambda message: None) -> None:
        from pilot.core.bench.runtime import BenchRuntime

        BenchRuntime(self).install_requirements(on_progress)

    def apply_saved_settings(
        self,
        old_restart: dict,
        old_firewall: dict,
        old_waf: dict,
        old_s3_config: dict,
    ) -> tuple[bool, str | None]:
        from pilot.core.bench.settings import BenchSettings

        return BenchSettings(self).apply_saved_settings(
            old_restart,
            old_firewall,
            old_waf,
            old_s3_config,
        )

    def restart_processes(self) -> None:
        from pilot.core.bench.production import BenchProduction

        BenchProduction(self).restart_processes()

    def remove_production(self, on_progress: Callable[[str], None] = lambda message: None) -> None:
        from pilot.core.bench.production import BenchProduction

        BenchProduction(self).remove_production(on_progress)

    def drop(self, on_progress: Callable[[str], None] = lambda message: None) -> None:
        from pilot.core.bench.production import BenchProduction

        BenchProduction(self).drop(on_progress)

    def ensure_no_sites(self) -> None:
        sites = self.sites()
        if sites:
            listed = ", ".join(s.config.name for s in sites)
            raise BenchError(
                f"Bench '{self.config.name}' still has {len(sites)} site(s): {listed}. "
                f"Drop them first, then retry."
            )

    def setup_nginx(self, on_progress: Callable[[str], None] = lambda message: None) -> None:
        from pilot.core.bench.production import BenchProduction

        BenchProduction(self).setup_nginx(on_progress)

    def setup_letsencrypt(self) -> None:
        from pilot.core.bench.production import BenchProduction

        BenchProduction(self).setup_letsencrypt()

    def initialize(self, on_progress: Callable[[str], None] = lambda message: None) -> None:
        from pilot.core.bench.initializer import BenchInitializer

        BenchInitializer(self).run(on_progress)

    def setup_production(
        self,
        process_manager: str | None = None,
        admin_domain: str | None = None,
        admin_tls: bool | None = None,
        letsencrypt_email: str | None = None,
        best_effort_tls: bool = False,
        on_progress: Callable[[str], None] = lambda message: None,
    ) -> None:
        from pilot.core.bench.production import BenchProduction

        BenchProduction(self).setup_production(
            process_manager=process_manager,
            admin_domain=admin_domain,
            admin_tls=admin_tls,
            letsencrypt_email=letsencrypt_email,
            best_effort_tls=best_effort_tls,
            on_progress=on_progress,
        )

    def reload_workers(self, web_only: bool = False, raises: bool = False):
        from pilot.managers.processes.local import ProcessManager

        try:
            ProcessManager.for_bench(self).reload_workers(web_only)
        except Exception as e:
            print(f"Failed to reload workers: {e}")
            if raises:
                raise

    def update(
        self,
        apps_filter: set | None = None,
        skip_failing_patches: bool = False,
        on_step: Callable[[str, str], None] = lambda key, label: None,
        on_progress: Callable[[str], None] = lambda message: None,
    ) -> None:
        """Update apps, dependencies, assets, sites, and workers."""
        on_step("fetch", "Fetching latest code")
        self._update_apps(apps_filter, on_progress)
        on_step("install", "Installing dependencies")
        self._reinstall_apps(apps_filter, on_progress)
        on_step("assets", "Building assets")
        self._rebuild_assets(apps_filter, on_progress)
        on_step("migrate", "Migrating sites")
        self._migrate_sites(skip_failing_patches, on_progress)
        on_step("restart", "Restarting services")
        self.reload_workers()
        on_step("done", "Done")

    def _update_apps(
        self,
        apps_filter: set | None,
        on_progress: Callable[[str], None],
        pins: dict[str, RevisionPin] | None = None,
    ) -> None:
        from pilot.core.bench.update import BenchUpdater

        BenchUpdater(self).update_apps(apps_filter, on_progress, pins)

    def _reinstall_apps(self, apps_filter: set | None, on_progress: Callable[[str], None]) -> None:
        from pilot.core.bench.update import BenchUpdater

        BenchUpdater(self).reinstall_apps(apps_filter, on_progress)

    def _rebuild_assets(self, apps_filter: set | None, on_progress: Callable[[str], None]) -> None:
        from pilot.core.bench.update import BenchUpdater

        BenchUpdater(self).rebuild_assets(apps_filter, on_progress)

    def _migrate_sites(self, skip_failing_patches: bool, on_progress: Callable[[str], None]) -> None:
        from pilot.core.bench.update import BenchUpdater

        BenchUpdater(self).migrate_sites(skip_failing_patches, on_progress)


def _marketplace_pin(app: "App", marketplace_by_name: dict) -> "RevisionPin | None":
    """Marketplace's advertised pin for app's installed version, or None for a
    branch target, unlisted app, or repo mismatch (e.g. a fork)."""
    from pilot.core.bench.update import marketplace_pin

    return marketplace_pin(app, marketplace_by_name)
