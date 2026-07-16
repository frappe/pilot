from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.commands.base import Command
from pilot.exceptions import BenchError
from pilot.secure_files import write_private_text

if TYPE_CHECKING:
    from pilot.core.bench import Bench
    from pilot.core.site import Site


class NewSiteCommand(Command):
    name = "new-site"
    help = "Create a new site and add it to bench.toml."

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("name", help="Site name (e.g. site2.localhost).")
        parser.add_argument("--admin-password", default="admin", help="Frappe admin password.")
        parser.add_argument("--apps", nargs="*", help="Apps to assign (defaults to framework app).")

    @classmethod
    def from_args(cls, args, bench):
        app_names = args.apps
        if not app_names:
            framework = bench.config.framework_app.name
            app_names = [framework] if framework else []
        return cls(bench, args.name, app_names, args.admin_password)

    def __init__(self, bench: "Bench", name: str, apps: list[str], admin_password: str, db_type: str | None = None) -> None:
        if not isinstance(admin_password, str) or not admin_password.strip():
            raise BenchError("Site Administrator password must not be empty.")
        self.bench = bench
        self.name = name  # type: ignore[misc]  # the new site's name, distinct from Command.name (the CLI verb)
        self.apps = apps
        self.admin_password = admin_password
        self.db_type = db_type
        self._via_wildcard = False

    def run(self) -> None:
        from pilot.config.site_config import SiteConfig
        from pilot.core.site import Site

        self._validate()
        ssl = self._should_enable_ssl()
        self._register_with_provider()
        site = Site(SiteConfig(name=self.name, apps=self.apps, admin_password=self.admin_password, ssl=ssl), self.bench)
        print(f"Creating site '{self.name}'...")
        sys.stdout.flush()
        site.create(db_type=self.db_type)
        self._install_apps(site)
        self._write_pilot_communication_config()
        self.bench.write_common_site_config()
        print(f"\nSite '{self.name}' created successfully.")
        self.build_missing_assets()
        self._add_to_hosts()
        self._reload_nginx()
        if ssl:
            self._obtain_cert(site)

    def _register_with_provider(self) -> None:
        """A wildcard-derived name is the provider's to allocate; provision it before
        creating the site so a provider failure leaves no orphan site."""
        if not self._via_wildcard:
            return
        from pilot.core.domains import DomainRouteProvider

        DomainRouteProvider(self.bench).register(self.name, self.name)

    def _install_apps(self, site: "Site") -> None:
        """`new-site` only installs the framework app; install the rest explicitly."""
        framework = self.bench.config.framework_app.name
        for app_name in self.apps:
            if app_name == framework:
                continue
            print(f"Installing app '{app_name}'...")
            sys.stdout.flush()
            site.install_app(self.bench.app(app_name))

    def _write_pilot_communication_config(self) -> None:
        import json

        from pilot.admin_url import admin_url
        from pilot.commands.admin.generate_session import ensure_jwt_secret, issue_site_token

        config_path = self.bench.sites_path / self.name / "site_config.json"
        if not config_path.exists():
            return
        config = json.loads(config_path.read_text())
        secret = ensure_jwt_secret(self.bench.path / "bench.toml")
        config["pilot_endpoint"] = admin_url(self.bench.config)
        config["pilot_auth_token"] = issue_site_token(secret, self.name, ttl=365 * 24 * 3600)
        write_private_text(config_path, json.dumps(config, indent=1))

    def build_missing_assets(self):
        from pilot.managers.python_environment import PythonEnvManager

        manager = PythonEnvManager(self.bench)
        assets_dir = self.bench.sites_path / "assets"

        for app in self.bench.apps():
            if not self.bench.is_app_installed(app.config.name):
                continue
            if not (assets_dir / app.config.name).exists():
                manager.build_assets_for_app(app)

    def _should_enable_ssl(self) -> bool:
        from pilot.managers.letsencrypt import _is_public_domain, letsencrypt_active

        return letsencrypt_active(self.bench) and _is_public_domain(self.name)

    def _obtain_cert(self, site) -> None:
        import json

        from pilot.managers.letsencrypt import LetsEncryptManager
        from pilot.managers.nginx import NginxManager

        if not self.bench.config.production.enabled:
            return

        # Persist ssl=True so that generate_config(ssl_ready=True) below
        # produces an HTTPS block for this site (bench.sites() reads from disk).
        config_path = self.bench.sites_path / self.name / "site_config.json"
        raw = json.loads(config_path.read_text()) if config_path.exists() else {}
        raw["ssl"] = True
        write_private_text(config_path, json.dumps(raw, indent=1))

        print("Obtaining SSL certificate...")
        sys.stdout.flush()
        nginx_mgr = NginxManager(self.bench)
        # Serve ACME challenges over HTTP before the cert exists.
        nginx_mgr.generate_config(ssl_ready=False)
        nginx_mgr.reload()
        LetsEncryptManager(self.bench).obtain(site.config)
        nginx_mgr.generate_config(ssl_ready=True)
        nginx_mgr.reload()

    def _validate(self) -> None:
        from pilot.core.domains import DomainRouteProvider
        from pilot.utils import host_owner, matches_wildcard, normalize_host

        if (self.bench.sites_path / self.name / "site_config.json").exists():
            raise BenchError(f"Site '{self.name}' already exists.")
        owner = host_owner(self.bench.path, self.name)
        if owner:
            raise BenchError(
                f"'{self.name}' is already used by bench '{owner}' (as a site or its admin domain). "
                f"All benches share one nginx, so hostnames must be unique."
            )
        if normalize_host(self.name) == normalize_host(self.bench.config.admin.domain):
            raise BenchError(
                f"Site '{self.name}' clashes with this bench's admin domain. "
                f"An admin domain must not match a site domain."
            )
        patterns = DomainRouteProvider.wildcard_domains()
        if patterns and not matches_wildcard(self.name, patterns):
            raise BenchError(f"Site name must match one of this bench's wildcard domains: {', '.join(patterns)}.")
        self._via_wildcard = bool(patterns)
        apps_txt = self.bench.sites_path / "apps.txt"
        installed = set(apps_txt.read_text().splitlines()) if apps_txt.exists() else set()
        for app in self.apps:
            if app not in installed:
                raise BenchError(f"App '{app}' is not installed. Run 'bench get-app <repo>' first.")

    def _add_to_hosts(self) -> None:
        # Only a dev bench (no process manager) needs a synthetic /etc/hosts entry;
        # production sites resolve via real DNS.
        if not self.bench.config.production.process_manager == "none":
            return

        hosts_path = Path("/etc/hosts")
        from pilot.utils import hosts_line_contains

        entry = f"127.0.0.1 {self.name}"
        for line in hosts_path.read_text().splitlines():
            if hosts_line_contains(line, self.name):
                return

        try:
            subprocess.run(
                ["sudo", "-n", "tee", "-a", str(hosts_path)],
                input=f"{entry}\n".encode(),
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, OSError) as e:
            print(
                f"Warning: could not add '{entry}' to {hosts_path}: {e}.\n"
                f"  Add it manually to reach the site by name.",
                file=sys.stderr,
            )

    def _reload_nginx(self) -> None:
        if not self.bench.config.production.enabled:
            return
        from pilot.managers.nginx import NginxManager

        mgr = NginxManager(self.bench)
        if not mgr.is_installed():
            return
        print("Updating nginx configuration...")
        sys.stdout.flush()
        mgr.generate_config(ssl_ready=True)
        mgr.reload()
