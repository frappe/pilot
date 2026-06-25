from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from bench_cli.platform import _privileged, get_package_manager, is_alpine
from bench_cli.utils import run_command

if TYPE_CHECKING:
    from bench_cli.config.site_config import SiteConfig
    from bench_cli.core.bench import Bench

_CERT_EXPIRY_THRESHOLD_DAYS = 30


def _nginx_reload_hook() -> str:
    """Shell command certbot runs after a successful (re)issue to pick up the new
    cert — rc-service on Alpine, systemctl elsewhere."""
    return "rc-service nginx reload" if is_alpine() else "systemctl reload nginx"


def _is_public_domain(domain: str) -> bool:
    """A domain certbot can actually validate over the public internet.
    Local dev domains (``*.localhost``) are excluded."""
    return bool(domain) and not domain.endswith(".localhost")


def public_domains(site: "SiteConfig") -> list[str]:
    """The site's domains certbot can issue for — the only ones a cert covers, so
    a site with an internal name but a public custom domain still gets TLS."""
    return [domain for domain in site.all_domains if _is_public_domain(domain)]


def cert_covers(cert_file: Path, domains: list[str]) -> bool:
    """True if the on-disk cert's SAN list already includes every domain."""
    import subprocess

    result = subprocess.run(
        _privileged(["openssl", "x509", "-noout", "-ext", "subjectAltName", "-in", str(cert_file)]),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    sans = {token.strip().removeprefix("DNS:") for token in result.stdout.replace(",", " ").split()}
    return all(domain in sans for domain in domains)


def letsencrypt_active(bench: "Bench") -> bool:
    """True if this bench is configured to obtain its own TLS certificates."""
    return bool(bench.config.letsencrypt.email) and bench.config.admin.tls


def letsencrypt_email_required(bench: "Bench") -> bool:
    """True if --tls would actually obtain a cert here — an SSL site or a public
    admin domain — and so needs a contact email. Local domains (``*.localhost``)
    never get certs, so no email is required for them.

    admin.tls = False means a central proxy terminates TLS for the whole bench,
    so no certs are obtained here at all.
    """
    if not bench.config.admin.tls:
        return False
    if any(site.config.ssl and _is_public_domain(site.config.name) for site in bench.sites()):
        return True
    return _is_public_domain(bench.config.admin.domain)


def needs_letsencrypt(bench: "Bench") -> bool:
    """True if any certificate is obtainable. Requires letsencrypt.email to be set."""
    return bool(bench.config.letsencrypt.email) and letsencrypt_email_required(bench)


class LetsEncryptManager:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def is_installed(self) -> bool:
        return shutil.which("certbot") is not None

    def install(self) -> None:
        if not self.is_installed():
            get_package_manager().install("certbot")

    def ensure_webroot(self) -> None:
        # /var/www is root-owned, so create the webroot with sudo. Default 0755
        # lets certbot (root) write ACME challenges and nginx read them.
        run_command(_privileged(["mkdir", "-p", str(self.bench.config.letsencrypt.webroot_path)]))

    def obtain(self, site: "SiteConfig") -> None:
        from bench_cli.managers.nginx_manager import NginxManager

        domains = public_domains(site)
        if not domains:
            return  # nothing certbot can validate over the public internet

        nginx_manager = NginxManager(self.bench)
        if (
            nginx_manager.cert_exists(site)
            and not self._is_near_expiry(site)
            and self._cert_covers(nginx_manager.cert_path(site), domains)
        ):
            print(f"Certificate for {site.name} already covers all domains and is not near expiry. Skipping.")
            return

        domain_args = []
        for domain in domains:
            domain_args.extend(["-d", domain])

        webroot_path = str(self.bench.config.letsencrypt.webroot_path)
        email = self.bench.config.letsencrypt.email

        run_command(_privileged([
            "certbot", "certonly",
            "--webroot",
            "-w", webroot_path,
            *domain_args,
            "--cert-name", site.name,
            "--expand",
            "--email", email,
            "--agree-tos",
            "--non-interactive",
            "--deploy-hook", _nginx_reload_hook(),
        ]))

    def obtain_all(self) -> None:
        # With TLS disabled a central proxy fronts the bench; obtain nothing.
        if not self.bench.config.admin.tls:
            return
        from bench_cli.exceptions import CommandError

        failed = []
        for site in self.bench.sites():
            if site.config.ssl and public_domains(site.config):
                try:
                    self.obtain(site.config)
                except CommandError as exc:
                    print(f"Could not obtain a certificate for '{site.config.name}', skipping: {exc}")
                    failed.append(site.config.name)
        if _is_public_domain(self.bench.config.admin.domain):
            try:
                self.obtain_admin()
            except CommandError as exc:
                print(f"Could not obtain a certificate for '{self.bench.config.admin.domain}', skipping: {exc}")
                failed.append(self.bench.config.admin.domain)
        # Don't raise: certs that did issue should still get TLS applied. Domains
        # that failed stay on HTTP and can be retried later.
        if failed:
            print(f"Certificate issuance failed for: {', '.join(failed)}. These stay on HTTP.")

    def obtain_admin(self) -> None:
        from bench_cli.managers.nginx_manager import NginxManager

        nginx_manager = NginxManager(self.bench)
        domain = self.bench.config.admin.domain

        if nginx_manager.admin_cert_exists() and not self._is_near_expiry_cert(nginx_manager.admin_cert_path()):
            print(f"Certificate for {domain} already exists and is not near expiry. Skipping.")
            return

        run_command(_privileged([
            "certbot", "certonly",
            "--webroot",
            "-w", str(self.bench.config.letsencrypt.webroot_path),
            "-d", domain,
            "--email", self.bench.config.letsencrypt.email,
            "--agree-tos",
            "--non-interactive",
            "--deploy-hook", _nginx_reload_hook(),
        ]))

    def renew(self) -> None:
        run_command(_privileged(["certbot", "renew", "--quiet"]))

    def _cert_covers(self, cert_file: Path, domains: list[str]) -> bool:
        return cert_covers(cert_file, domains)

    def _is_near_expiry(self, site: "SiteConfig") -> bool:
        from bench_cli.managers.nginx_manager import NginxManager

        nginx_manager = NginxManager(self.bench)
        return self._is_near_expiry_cert(nginx_manager.cert_path(site))

    def _is_near_expiry_cert(self, cert_file: Path) -> bool:
        import subprocess
        from datetime import datetime, timezone

        result = subprocess.run(
            _privileged(["openssl", "x509", "-enddate", "-noout", "-in", str(cert_file)]),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return True

        date_str = result.stdout.strip().replace("notAfter=", "")
        expiry = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
        now = datetime.now(tz=timezone.utc)
        return (expiry - now).days < _CERT_EXPIRY_THRESHOLD_DAYS
