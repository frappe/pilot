from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING

from pilot.config.site import SiteConfig
from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.bench import Bench
    from pilot.core.site import Site


class SiteProvisioner:
    def __init__(
        self,
        bench: "Bench",
        name: str,
        apps: list[str],
        admin_password: str,
        db_type: str | None = None,
    ) -> None:
        self.bench = bench
        self.name = name
        self.apps = apps
        self.admin_password = admin_password
        self.db_type = db_type

    def provision(self, on_progress: Callable[[str], None]) -> "Site":
        from pilot.core.site import Site

        via_wildcard = validate_new_site(self.bench, self.name, self.apps)
        ssl = should_enable_ssl(self.bench, self.name)
        if via_wildcard:
            register_with_provider(self.bench, self.name)

        site = Site(
            SiteConfig(
                name=self.name,
                apps=self.apps,
                admin_password=self.admin_password,
                ssl=ssl,
            ),
            self.bench,
        )
        on_progress(f"Creating site '{self.name}'...")
        site.create(db_type=self.db_type)
        self.install_apps(site, on_progress)
        self.write_pilot_communication_config(site)
        self.bench.write_common_site_config()
        on_progress(f"\nSite '{self.name}' created successfully.")
        self.build_missing_assets()
        self.add_to_hosts(site)
        self.reload_nginx()
        if ssl:
            self.obtain_cert(site, on_progress)
        return site

    def install_apps(self, site: "Site", on_progress: Callable[[str], None]) -> None:
        framework = self.bench.config.framework_app.name
        for app_name in self.apps:
            if app_name == framework:
                continue
            on_progress(f"Installing app '{app_name}'...")
            site.install_app(self.bench.app(app_name))

    def write_pilot_communication_config(self, site: "Site") -> None:
        from pilot.admin_url import admin_url
        from pilot.core.admin_auth import ensure_jwt_secret, issue_site_token
        from pilot.secure_files import write_private_text

        config_path = site.path / "site_config.json"
        if not config_path.exists():
            return
        config = json.loads(config_path.read_text())
        secret = ensure_jwt_secret(self.bench.path / "bench.toml")
        config["pilot_endpoint"] = admin_url(self.bench.config)
        config["pilot_auth_token"] = issue_site_token(
            secret,
            site.config.name,
            ttl=365 * 24 * 3600,
        )
        write_private_text(config_path, json.dumps(config, indent=1))

    def build_missing_assets(self) -> None:
        from pilot.managers.python_environment import PythonEnvManager

        manager = PythonEnvManager(self.bench)
        assets_dir = self.bench.sites_path / "assets"
        for app in self.bench.apps():
            if not self.bench.is_app_installed(app.config.name):
                continue
            if not (assets_dir / app.config.name).exists():
                manager.build_assets_for_app(app)

    def add_to_hosts(self, site: "Site") -> None:
        if not self.bench.config.production.process_manager == "none":
            return

        from pilot.managers.platform import add_hosts_entry

        add_hosts_entry(site.config.name)

    def reload_nginx(self) -> None:
        from pilot.managers.nginx import NginxManager

        NginxManager(self.bench).reload_for_site_change()

    def obtain_cert(self, site: "Site", on_progress: Callable[[str], None]) -> None:
        from pilot.managers.letsencrypt import LetsEncryptManager
        from pilot.managers.nginx import NginxManager

        if not self.bench.config.production.enabled:
            return

        site.set_ssl(True)
        on_progress("Obtaining SSL certificate...")
        nginx_manager = NginxManager(self.bench)
        nginx_manager.generate_config(ssl_ready=False)
        nginx_manager.reload()
        LetsEncryptManager(self.bench).obtain(site.config)
        nginx_manager.generate_config(ssl_ready=True)
        nginx_manager.reload()


def validate_new_site(bench: "Bench", name: str, apps: list[str]) -> bool:
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
        raise BenchError(
            f"Site name must match one of this bench's wildcard domains: {', '.join(patterns)}."
        )
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
    if not isinstance(admin_password, str) or not admin_password.strip():
        raise BenchError("Site Administrator password must not be empty.")
    from pilot.core.site import Site

    site = Site.provision(bench, name, [], admin_password, on_progress=on_progress)
    on_progress(f"Restoring backup: {db_file}")
    site.restore(db_file, public_files, private_files)
    return site


def should_enable_ssl(bench: "Bench", name: str) -> bool:
    from pilot.managers.letsencrypt import _is_public_domain, letsencrypt_active

    return letsencrypt_active(bench) and _is_public_domain(name)


def register_with_provider(bench: "Bench", name: str) -> None:
    from pilot.core.domains import DomainRouteProvider

    DomainRouteProvider(bench).register(name, name)
