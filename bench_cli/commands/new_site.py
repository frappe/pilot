from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from bench_cli.config.site_config import SiteConfig
from bench_cli.core.site import Site
from bench_cli.exceptions import BenchError

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench


class NewSiteCommand:
    def __init__(self, bench: "Bench", name: str, apps: list[str], admin_password: str = "admin") -> None:
        self.bench = bench
        self.name = name
        self.apps = apps
        self.admin_password = admin_password

    def run(self) -> None:
        self._validate()
        site = Site(SiteConfig(name=self.name, apps=self.apps, admin_password=self.admin_password), self.bench)
        print(f"Creating site '{self.name}'...")
        sys.stdout.flush()
        site.create()
        self.bench.write_common_site_config()
        print(f"\nSite '{self.name}' created successfully.")
        self._reload_nginx()

    def _validate(self) -> None:
        if (self.bench.sites_path / self.name / "site_config.json").exists():
            raise BenchError(f"Site '{self.name}' already exists.")
        apps_txt = self.bench.sites_path / "apps.txt"
        installed = set(apps_txt.read_text().splitlines()) if apps_txt.exists() else set()
        for app in self.apps:
            if app not in installed:
                raise BenchError(f"App '{app}' is not installed. Run 'bench get-app <repo>' first.")

    def _reload_nginx(self) -> None:
        if not self.bench.config.production.nginx:
            return
        from bench_cli.managers.nginx_manager import NginxManager
        mgr = NginxManager(self.bench)
        if not mgr.is_installed():
            return
        print("Updating nginx configuration...")
        sys.stdout.flush()
        mgr.generate_config(ssl_ready=True)
        mgr.reload()
