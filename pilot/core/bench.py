from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, List

from pilot.config.bench import BenchConfig
from pilot.secure_files import write_private_text
from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.config.s3 import S3Config
    from pilot.core.app import App, RevisionPin
    from pilot.core.database import Database
    from pilot.core.site import Site


class Bench:
    def __init__(self, config: BenchConfig, path: Path) -> None:
        self.config = config
        self.path = path
        self._db: "Database | None" = None

    @property
    def db(self) -> "Database":
        if self._db is None:
            from pilot.core.database import make_database
            self._db = make_database(self.config)
        return self._db

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

    def db_root_args(self) -> list[str]:
        """Root username/password for site teardown/restore/reinstall, keyed to the
        bench's engine (every site on a bench shares one engine)."""
        if self.config.db_type == "postgres":
            pg = self.config.postgres
            return ["--db-root-username", pg.admin_user, "--db-root-password", self.postgres_root_password()]
        if self.config.db_type == "sqlite":
            return []
        mariadb = self.config.mariadb
        return ["--db-root-username", mariadb.admin_user, "--db-root-password", mariadb.root_password]

    def postgres_root_password(self) -> str:
        # frappe's postgres setup falls back to an interactive getpass() — which
        # hangs the background task — when the password is empty. Pass a non-empty
        # placeholder instead: trust/peer auth ignores it, while a password-auth
        # server returns a clear authentication error.
        return self.config.postgres.root_password or "trust_auth"

    def app(self, name: str) -> "App":
        """Return an App for a cloned app folder by name or module name.
        Accepts either the folder name or the module name for parity with original bench commands.
        """
        from pilot.config.app import AppConfig
        from pilot.core.app import App

        d = self.apps_path / name
        if not d.is_dir():
            # Try the hyphen variant so module names resolve to their folder.
            d = self.apps_path / name.replace("_", "-")

        if not d.is_dir():
            raise BenchError(f"App {name} not found in bench")

        return App(AppConfig(name=d.name, repo=self._git_remote(d), branch=self._git_branch(d)), self)

    def apps(self) -> List["App"]:
        """Return all cloned apps by scanning apps/ directory."""
        from pilot.config.app import AppConfig
        from pilot.core.app import App

        if not self.apps_path.is_dir():
            return []
        result = []
        for d in sorted(self.apps_path.iterdir()):
            if d.is_dir() and (d / ".git").exists():
                app_config = AppConfig(
                    name=d.name,
                    repo=self._git_remote(d),
                    branch=self._git_branch(d),
                )
                result.append(App(app_config, self))
        return result

    def registered_apps(self) -> List[str]:
        """Module names listed in sites/apps.txt — the bench-wide record of
        which apps are actually installed, as opposed to merely cloned."""
        apps_txt = self.sites_path / "apps.txt"
        return apps_txt.read_text().splitlines() if apps_txt.exists() else []

    def is_app_installed(self, name: str) -> bool:
        """Whether `name` (raw or module form) is installed on this bench —
        i.e. listed in apps.txt, not just cloned under apps/."""
        from pilot.config.app import AppConfig
        from pilot.core.app import App

        module_name = App(AppConfig(name=name, repo="", branch=""), self).module_name
        return module_name in self.registered_apps()

    def init_apps(self) -> List["App"]:
        """Return apps declared in bench.toml (used only during bench init)."""
        from pilot.core.app import App

        return [App(app_config, self) for app_config in self.config.apps]

    def sites(self) -> List["Site"]:
        """Return all sites by scanning sites/ directory."""
        import json as _json

        from pilot.config.site import SiteConfig
        from pilot.core.site import Site

        if not self.sites_path.is_dir():
            return []
        result = []
        for site_dir in sorted(self.sites_path.iterdir()):
            cfg_path = site_dir / "site_config.json"
            if site_dir.is_dir() and cfg_path.exists():
                try:
                    raw = _json.loads(cfg_path.read_text())
                except (OSError, _json.JSONDecodeError):
                    raw = {}
                raw_domains = [(entry.get("domain") if isinstance(entry, dict) else entry) for entry in (raw.get("domains") or [])]
                domains = [domain for domain in raw_domains if isinstance(domain, str) and domain]
                host_name = (raw.get("host_name") or "").strip()
                primary = host_name.split("://", 1)[-1] if host_name else ""
                site_config = SiteConfig(name=site_dir.name, apps=[], ssl=bool(raw.get("ssl")), domains=domains, primary_domain=primary)
                result.append(Site(site_config, self))
        return result

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
        """Write apps.txt from currently cloned apps in apps/ directory."""
        apps_txt = self.sites_path / "apps.txt"
        names = [app.config.name for app in self.apps()]
        apps_txt.write_text("\n".join(names) + "\n" if names else "")

    def set_maintenance_mode(self, enabled: bool) -> None:
        config_path = self.sites_path / "common_site_config.json"
        config = json.loads(config_path.read_text())
        config["maintenance_mode"] = 1 if enabled else 0
        write_private_text(config_path, json.dumps(config, indent=2))

    def sync_s3_credentials(self, s3_config: S3Config):
        config_path = self.sites_path / "common_site_config.json"
        if not config_path.exists():
            return

        config = json.loads(config_path.read_text())
        config["s3_access_key"] = s3_config.access_key
        config["s3_bucket"] = s3_config.bucket
        config["s3_secret_key"] = s3_config.secret_key
        config["s3_provider"] = s3_config.provider
        config["s3_region"] = s3_config.region
        write_private_text(config_path, json.dumps(config, indent=2) + "\n")

    def write_common_site_config(self) -> None:
        r = self.config.redis
        redis_cache = f"redis://localhost:{r.cache_port}"
        redis_queue = f"redis://localhost:{r.queue_port}"
        redis_socketio = redis_cache
        # Enable monitoring by default on all the sites on the bench
        config = {
            "redis_cache": redis_cache,
            "redis_queue": redis_queue,
            "redis_socketio": redis_socketio,
            "socketio_port": self.config.socketio_port,
            "webserver_port": self.config.http_port,
            "socketio_backend": self.config.socketio_backend,
            "monitor": True,
        }
        config_path = self.sites_path / "common_site_config.json"
        write_private_text(config_path, json.dumps(config, indent=2) + "\n")

    def restart(self):
        """Restart bench in case we are running in production"""
        self.restart_processes()

    def restart_processes(self) -> None:
        if not self.config.production.enabled:
            return
        from typing import cast

        from pilot.managers.processes.base import ManagedProcessManager
        from pilot.managers.processes.local import ProcessManager

        # production.enabled is already confirmed above, so for_bench() always
        # returns a ManagedProcessManager subclass here, never the plain base.
        manager = cast(ManagedProcessManager, ProcessManager.for_bench(self))
        if not manager.is_configured():
            return
        manager.write_config()
        manager.reload_manager_config()
        manager.restart()

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
        """Pull latest code, reinstall, rebuild assets, migrate every site,
        then reload workers. `on_step` marks the start of each phase;
        `on_progress` reports per-app/per-site lines within a phase."""
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

    def _update_apps(self, apps_filter: set | None, on_progress: Callable[[str], None]) -> None:
        import sys

        from pilot.exceptions import CommandError, MigrateError
        from pilot.integrations.marketplace import Marketplace

        marketplace_by_name = {entry["name"]: entry for entry in Marketplace.registry()}

        for app in self.apps():
            if apps_filter is not None and app.config.name not in apps_filter:
                continue
            on_progress(f"Updating {app.config.name}...")
            try:
                app.update(pin=_marketplace_pin(app, marketplace_by_name))
            except CommandError as e:
                print(f"  Error updating {app.config.name}: {e}", file=sys.stderr)
                raise MigrateError(f"Failed to update {app.config.name}")

    def _reinstall_apps(self, apps_filter: set | None, on_progress: Callable[[str], None]) -> None:
        from pilot.exceptions import CommandError, MigrateError
        from pilot.managers.python_environment import PythonEnvManager

        mgr = PythonEnvManager(self)
        for app in self.apps():
            if apps_filter is not None and app.config.name not in apps_filter:
                continue
            on_progress(f"Reinstalling {app.config.name}...")
            try:
                mgr.install_app(app)
            except CommandError as e:
                raise MigrateError(f"Failed to install app {app}: {e}")

    def _rebuild_assets(self, apps_filter: set | None, on_progress: Callable[[str], None]) -> None:
        from pilot.managers.python_environment import PythonEnvManager

        mgr = PythonEnvManager(self)
        for app in self.apps():
            if apps_filter is not None and app.config.name not in apps_filter:
                continue
            on_progress(f"Updating assets for {app.config.name}...")
            mgr.build_assets_for_app(app)

    def _migrate_sites(self, skip_failing_patches: bool, on_progress: Callable[[str], None]) -> None:
        from pilot.exceptions import CommandError, MigrateError

        for site in self.sites():
            on_progress(f"Migrating {site.config.name}...")
            try:
                site.migrate(skip_failing=skip_failing_patches)
            except CommandError as e:
                raise MigrateError(f"Migration failed for {site.config.name}") from e

    @staticmethod
    def _git_remote(path: Path) -> str:
        from pilot.internal.git import GitRepo

        return GitRepo(path).remote_url

    @staticmethod
    def _git_branch(path: Path) -> str:
        from pilot.internal.git import GitRepo

        return GitRepo(path).branch


def _marketplace_pin(app: "App", marketplace_by_name: dict) -> "RevisionPin | None":
    """Marketplace's advertised pin for app's installed version, or None for a
    branch target, unlisted app, or repo mismatch (e.g. a fork)."""
    entry = marketplace_by_name.get(app.config.name)
    if not entry or app.config.repo != entry.get("repo"):
        return None
    version = app.installed_version
    target = next((t for t in entry.get("targets", []) if t["version"] == version), None)
    if target is None:
        return None

    from pilot.core.app import RevisionPin

    return RevisionPin.from_marketplace_target(target)
