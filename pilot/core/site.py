from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config.site import SiteConfig
from pilot.exceptions import BenchError
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
        if not isinstance(self.config.admin_password, str) or not self.config.admin_password.strip():
            raise BenchError("Site Administrator password must not be empty.")
        cmd = self._frappe_call("frappe", "--site", self.config.name, "new-site", self.config.name)
        cmd += ["--admin-password", self.config.admin_password]
        effective = db_type or self.bench.config.db_type
        if effective == "postgres":
            cmd += self._postgres_db_args()
        elif effective == "sqlite":
            cmd += self._sqlite_db_args()
        else:
            from pilot.managers.mariadb import MariaDBManager

            socket_path = MariaDBManager(self.bench.config.mariadb)._detect_socket()
            cmd += self._mariadb_db_args(socket_path)
        run_command(cmd, cwd=self.bench.sites_path, stream_output=True)

    def _mariadb_db_args(self, socket_path: str) -> list[str]:
        mariadb = self.bench.config.mariadb
        args = ["--db-root-username", mariadb.admin_user]
        if socket_path:
            args += ["--db-socket", socket_path]
            # unix_socket auth ignores the password; pass a non-empty placeholder
            # so frappe doesn't fall back to an interactive getpass() prompt
            args += ["--db-root-password", mariadb.root_password or "socket_auth"]
        else:
            args += ["--db-host", mariadb.host, "--db-port", str(mariadb.port)]
            if mariadb.root_password:
                args += ["--db-root-password", mariadb.root_password]
        return args

    def _postgres_db_args(self) -> list[str]:
        postgres = self.bench.config.postgres
        return [
            "--db-type", "postgres",
            "--db-host", postgres.host,
            "--db-port", str(postgres.port),
            "--db-root-username", postgres.admin_user,
            "--db-root-password", self.bench.postgres_root_password(),
        ]

    def _sqlite_db_args(self) -> list[str]:
        return ["--db-type", "sqlite"]

    def restore(self, db_file: str, public_files: str | None = None, private_files: str | None = None) -> None:
        cmd = self._frappe_call("frappe", "--site", self.config.name, "restore", db_file)
        if public_files:
            cmd += ["--with-public-files", public_files]
        if private_files:
            cmd += ["--with-private-files", private_files]
        # restore reads the engine from the site's config (frappe.init); it only
        # needs the matching root credentials, not a --db-type flag.
        cmd += self.bench.db_root_args()
        run_command(cmd, cwd=self.bench.sites_path, stream_output=True)

    def reinstall(self, admin_password: str) -> None:
        if not isinstance(admin_password, str) or not admin_password.strip():
            raise BenchError("Site Administrator password must not be empty.")
        cmd = self._frappe_call("frappe", "--site", self.config.name, "reinstall", "--yes", "--admin-password", admin_password)
        cmd += self.bench.db_root_args()
        run_command(cmd, cwd=self.bench.sites_path, stream_output=True)

    def install_app(self, app: "App") -> None:
        run_command(
            self._frappe_call("frappe", "--site", self.config.name, "install-app", app.config.name),
            cwd=self.bench.sites_path,
            stream_output=True,
        )
        self.bench.reload_workers(raises=True)

    def uninstall_app(self, app: "App", force: bool = False) -> None:
        cmd = self._frappe_call("frappe", "--site", self.config.name, "uninstall-app", app.config.name, "--yes", "--no-backup")
        if force:
            cmd.append("--force")
        run_command(cmd, cwd=self.bench.sites_path, stream_output=True)
        self.bench.reload_workers(raises=True)

    def list_apps(self) -> list[str]:
        import subprocess

        result = subprocess.run(
            self._frappe_call("frappe", "--site", self.config.name, "list-apps"),
            cwd=str(self.bench.sites_path),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        return [line.split()[0] for line in result.stdout.splitlines() if line.strip()]

    def installed_apps(self) -> list[str]:
        """Installed app names for this site, using the fastest available
        method (site_config.json's cache, then a direct DB query, then a
        frappe subprocess) — cheaper than list_apps() for read-heavy callers
        like the admin API."""
        import json

        config_path = self.path / "site_config.json"
        if not config_path.exists():
            raise BenchError(f"Site '{self.config.name}' does not exist.")
        try:
            site_config = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError):
            site_config = {}
        return list_installed_apps(site_config, self.bench.path, self.config.name)

    def migrate(self, skip_failing: bool = False) -> None:
        cmd = self._frappe_call("frappe", "--site", self.config.name, "migrate")
        if skip_failing:
            cmd.append("--skip-failing")
        run_command(cmd, cwd=self.bench.sites_path, stream_output=True)

    def uninstall_apps(
        self,
        app_names: list[str],
        force: bool = False,
        on_progress: Callable[[str], None] = lambda message: None,
    ) -> None:
        """Uninstall each app from this site, then remove any of them from the
        bench entirely if they end up installed on no site at all."""
        if not self.exists:
            raise BenchError(f"Site '{self.config.name}' does not exist.")

        installed = self.list_apps()
        for app_name in app_names:
            app = self.bench.app(app_name)
            if not force and installed and app.config.name not in installed:
                raise BenchError(f"App '{app_name}' is not installed on site '{self.config.name}'.")
            on_progress(f"Uninstalling '{app_name}' from site '{self.config.name}'...")
            self.uninstall_app(app, force=force)
            on_progress(f"'{app_name}' uninstalled from '{self.config.name}'.")
            self._remove_app_if_not_on_any_site(app_name, on_progress)

    def _remove_app_if_not_on_any_site(self, app_name: str, on_progress: Callable[[str], None]) -> None:
        for site in self.bench.sites():
            installed_apps = site.list_apps()
            if len(installed_apps) == 0 or app_name in installed_apps:
                return
        on_progress(f"\nApp {app_name} is not installed on any site removing from bench.")
        self.bench.app(app_name).remove(on_progress=on_progress)

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
        from pilot.core.domains import DomainRouteProvider

        if not (self.path / "site_config.json").exists():
            return []
        return [self.config.name, *DomainRouteProvider(self.bench).domains(self.config.name)]

    def _release_domains(self, domains: list[str]) -> None:
        """Release the captured domains at the provider, only after the drop has
        succeeded. Best effort: a teardown failure leaves a stale route, but the
        site is already gone so it must not turn a successful drop into an error."""
        if not domains:
            return
        from pilot.core.domains import DomainRouteProvider

        routes = DomainRouteProvider(self.bench)
        for domain in domains:
            routes.release(domain)

    def _remove_from_bench_toml(self) -> None:
        from pilot.config.toml_store import BenchTomlStore

        store = BenchTomlStore.for_bench(self.bench.path)
        with store.edit_raw() as raw:
            raw["sites"] = [s for s in raw.get("sites", []) if s.get("name") != self.config.name]

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
        via_wildcard = _validate_new_site(bench, name, apps)
        ssl = _should_enable_ssl(bench, name)
        if via_wildcard:
            _register_with_provider(bench, name)

        site = cls(SiteConfig(name=name, apps=apps, admin_password=admin_password, ssl=ssl), bench)
        on_progress(f"Creating site '{name}'...")
        site.create(db_type=db_type)
        site._install_apps(apps, on_progress)
        site._write_pilot_communication_config()
        bench.write_common_site_config()
        on_progress(f"\nSite '{name}' created successfully.")
        site._build_missing_assets()
        site._add_to_hosts()
        from pilot.managers.nginx import NginxManager

        NginxManager(bench).reload_for_site_change()
        if ssl:
            site._obtain_cert(on_progress)
        return site

    def _install_apps(self, apps: list[str], on_progress: Callable[[str], None]) -> None:
        """`new-site` only installs the framework app; install the rest explicitly."""
        framework = self.bench.config.framework_app.name
        for app_name in apps:
            if app_name == framework:
                continue
            on_progress(f"Installing app '{app_name}'...")
            self.install_app(self.bench.app(app_name))

    def _write_pilot_communication_config(self) -> None:
        import json

        from pilot.admin_url import admin_url
        from pilot.core.admin_auth import ensure_jwt_secret, issue_site_token
        from pilot.secure_files import write_private_text

        config_path = self.path / "site_config.json"
        if not config_path.exists():
            return
        config = json.loads(config_path.read_text())
        secret = ensure_jwt_secret(self.bench.path / "bench.toml")
        config["pilot_endpoint"] = admin_url(self.bench.config)
        config["pilot_auth_token"] = issue_site_token(secret, self.config.name, ttl=365 * 24 * 3600)
        write_private_text(config_path, json.dumps(config, indent=1))

    def _build_missing_assets(self) -> None:
        from pilot.managers.python_environment import PythonEnvManager

        manager = PythonEnvManager(self.bench)
        assets_dir = self.bench.sites_path / "assets"
        for app in self.bench.apps():
            if not self.bench.is_app_installed(app.config.name):
                continue
            if not (assets_dir / app.config.name).exists():
                manager.build_assets_for_app(app)

    def _add_to_hosts(self) -> None:
        # Only a dev bench (no process manager) needs a synthetic /etc/hosts entry;
        # production sites resolve via real DNS.
        if not self.bench.config.production.process_manager == "none":
            return

        from pilot.managers.platform import add_hosts_entry

        add_hosts_entry(self.config.name)

    def _obtain_cert(self, on_progress: Callable[[str], None]) -> None:
        import json

        from pilot.managers.letsencrypt import LetsEncryptManager
        from pilot.managers.nginx import NginxManager
        from pilot.secure_files import write_private_text

        if not self.bench.config.production.enabled:
            return

        # Persist ssl=True so that generate_config(ssl_ready=True) below
        # produces an HTTPS block for this site (bench.sites() reads from disk).
        config_path = self.path / "site_config.json"
        raw = json.loads(config_path.read_text()) if config_path.exists() else {}
        raw["ssl"] = True
        write_private_text(config_path, json.dumps(raw, indent=1))

        on_progress("Obtaining SSL certificate...")
        nginx_mgr = NginxManager(self.bench)
        # Serve ACME challenges over HTTP before the cert exists.
        nginx_mgr.generate_config(ssl_ready=False)
        nginx_mgr.reload()
        LetsEncryptManager(self.bench).obtain(self.config)
        nginx_mgr.generate_config(ssl_ready=True)
        nginx_mgr.reload()


def _validate_new_site(bench: "Bench", name: str, apps: list[str]) -> bool:
    """Validate a candidate new-site name/app-list; returns whether the name
    was matched via one of the bench's wildcard domains (which then must be
    registered with the domain provider before creation)."""
    from pilot.core.domains import DomainRouteProvider
    from pilot.utils import host_owner, matches_wildcard, normalize_host

    if (bench.sites_path / name / "site_config.json").exists():
        raise BenchError(f"Site '{name}' already exists.")
    owner = host_owner(bench.path, name)
    if owner:
        raise BenchError(
            f"'{name}' is already used by bench '{owner}' (as a site or its admin domain). "
            f"All benches share one nginx, so hostnames must be unique."
        )
    if normalize_host(name) == normalize_host(bench.config.admin.domain):
        raise BenchError(
            f"Site '{name}' clashes with this bench's admin domain. "
            f"An admin domain must not match a site domain."
        )
    patterns = DomainRouteProvider.wildcard_domains()
    if patterns and not matches_wildcard(name, patterns):
        raise BenchError(f"Site name must match one of this bench's wildcard domains: {', '.join(patterns)}.")
    apps_txt = bench.sites_path / "apps.txt"
    installed = set(apps_txt.read_text().splitlines()) if apps_txt.exists() else set()
    for app in apps:
        if app not in installed:
            raise BenchError(f"App '{app}' is not installed. Run 'bench get-app <repo>' first.")
    return bool(patterns)


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
    if not isinstance(admin_password, str) or not admin_password.strip():
        raise BenchError("Site Administrator password must not be empty.")
    site = Site.provision(bench, name, [], admin_password, on_progress=on_progress)
    on_progress(f"Restoring backup: {db_file}")
    site.restore(db_file, public_files, private_files)
    return site


def _should_enable_ssl(bench: "Bench", name: str) -> bool:
    from pilot.managers.letsencrypt import _is_public_domain, letsencrypt_active

    return letsencrypt_active(bench) and _is_public_domain(name)


def _register_with_provider(bench: "Bench", name: str) -> None:
    """A wildcard-derived name is the provider's to allocate; provision it before
    creating the site so a provider failure leaves no orphan site."""
    from pilot.core.domains import DomainRouteProvider

    DomainRouteProvider(bench).register(name, name)


_DB_SOCKET_CANDIDATES = [
    "/var/run/mysqld/mysqld.sock",
    "/run/mysqld/mysqld.sock",
    "/tmp/mysql.sock",
    "/usr/local/var/mysql/mysql.sock",
]


def list_installed_apps(site_config: dict, bench_root: Path, site_name: str) -> list[str]:
    """Return installed app names for a site, using the fastest available method."""
    # Fast path: frappe keeps this in sync after install/uninstall (v16+).
    if isinstance(site_config.get("installed_apps"), list):
        return site_config["installed_apps"]
    # Fallback: query DB directly, then frappe subprocess.
    apps = query_installed_apps_via_db(site_config)
    if apps is not None:
        return apps
    return _query_installed_apps_via_frappe(bench_root, site_name)


def query_installed_apps_via_db(site_config: dict) -> list[str] | None:
    import subprocess

    db_name = site_config.get("db_name", "")
    db_password = site_config.get("db_password", "")
    db_host = site_config.get("db_host") or "localhost"
    db_port = int(site_config.get("db_port") or 3306)
    if not db_name or not db_password:
        return None

    import shutil

    cli = shutil.which("mariadb") or shutil.which("mysql")
    if not cli:
        return None

    conn_args = [f"--user={db_name}", f"--password={db_password}"]
    if db_host in ("localhost", "127.0.0.1", ""):
        socket_path = next((s for s in _DB_SOCKET_CANDIDATES if Path(s).exists()), None)
        if socket_path:
            conn_args.append(f"--socket={socket_path}")
        else:
            conn_args += [f"--host=127.0.0.1", f"--port={db_port}"]
    else:
        conn_args += [f"--host={db_host}", f"--port={db_port}"]

    try:
        result = subprocess.run(
            [
                cli, *conn_args,
                "--batch", "--skip-column-names",
                db_name,
                "-e", "SELECT app_name FROM `tabInstalled Application` ORDER BY idx",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return None


def _query_installed_apps_via_frappe(bench_root: Path, site_name: str) -> list[str]:
    import os
    import subprocess

    python = str(bench_root / "env" / "bin" / "python")
    sites_dir = str(bench_root / "sites")
    try:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [python, "-m", "frappe.utils.bench_helper", "frappe", "--site", site_name, "list-apps"],
            cwd=sites_dir,
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
        if result.returncode != 0:
            return []
        return [line.split()[0] for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return []
