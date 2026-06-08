from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from bench_cli.platform import get_package_manager
from bench_cli.utils import run_command

if TYPE_CHECKING:
    from bench_cli.config.site_config import SiteConfig
    from bench_cli.core.bench import Bench

_CERT_EXPIRY_THRESHOLD_DAYS = 30


class LetsEncryptManager:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def is_installed(self) -> bool:
        return shutil.which("certbot") is not None

    def install(self) -> None:
        if not self.is_installed():
            get_package_manager().install("certbot")

    def ensure_webroot(self) -> None:
        self.bench.config.letsencrypt.webroot_path.mkdir(parents=True, exist_ok=True)

    def obtain(self, site: "SiteConfig") -> None:
        from bench_cli.managers.nginx_manager import NginxManager

        nginx_manager = NginxManager(self.bench)
        if nginx_manager.cert_exists(site) and not self._is_near_expiry(site):
            print(f"Certificate for {site.name} already exists and is not near expiry. Skipping.")
            return

        domain_args = []
        for domain in site.all_domains:
            domain_args.extend(["-d", domain])

        webroot_path = str(self.bench.config.letsencrypt.webroot_path)
        email = self.bench.config.letsencrypt.email

        run_command([
            "certbot", "certonly",
            "--webroot",
            "-w", webroot_path,
            *domain_args,
            "--email", email,
            "--agree-tos",
            "--non-interactive",
            "--deploy-hook", "systemctl reload nginx",
        ])

    def obtain_all(self) -> None:
        for site in self.bench.sites():
            if site.config.ssl:
                self.obtain(site.config)
        if self.bench.config.admin.domain:
            self.obtain_admin()

    def obtain_admin(self) -> None:
        from bench_cli.managers.nginx_manager import NginxManager

        nginx_manager = NginxManager(self.bench)
        domain = self.bench.config.admin.domain

        if nginx_manager.admin_cert_exists() and not self._is_near_expiry_cert(nginx_manager.admin_cert_path()):
            print(f"Certificate for {domain} already exists and is not near expiry. Skipping.")
            return

        run_command([
            "certbot", "certonly",
            "--webroot",
            "-w", str(self.bench.config.letsencrypt.webroot_path),
            "-d", domain,
            "--email", self.bench.config.letsencrypt.email,
            "--agree-tos",
            "--non-interactive",
            "--deploy-hook", "systemctl reload nginx",
        ])

    def renew(self) -> None:
        run_command(["certbot", "renew", "--quiet"])

    def _is_near_expiry(self, site: "SiteConfig") -> bool:
        from bench_cli.managers.nginx_manager import NginxManager

        nginx_manager = NginxManager(self.bench)
        return self._is_near_expiry_cert(nginx_manager.cert_path(site))

    def _is_near_expiry_cert(self, cert_file: Path) -> bool:
        import subprocess
        from datetime import datetime, timezone

        result = subprocess.run(
            ["openssl", "x509", "-enddate", "-noout", "-in", str(cert_file)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return True

        date_str = result.stdout.strip().replace("notAfter=", "")
        expiry = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        now = datetime.now(tz=timezone.utc)
        return (expiry - now).days < _CERT_EXPIRY_THRESHOLD_DAYS
