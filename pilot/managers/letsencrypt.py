from __future__ import annotations

import pwd
import shutil
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.managers.nginx import LETSENCRYPT_LIVE
from pilot.managers.packages import get_package_manager
from pilot.managers.platform import _privileged, which
from pilot.managers.sudoers import has_passwordless_sudo_for, install_sudoers_grant
from pilot.utils import run_command

if TYPE_CHECKING:
    from pilot.config import SiteConfig
    from pilot.core.bench import Bench
    from pilot.core.site import Site

_CERT_EXPIRY_THRESHOLD_DAYS = 30


def _nginx_reload_hook() -> str:
    """Return the certbot deploy hook that reloads nginx."""
    return "systemctl reload nginx"


def is_public_domain(domain: str) -> bool:
    """Whether certbot can validate the domain publicly."""
    domain = domain.strip().lower().rstrip(".")
    return bool(domain) and domain not in {"local", "localhost"} and not domain.endswith(
        (".local", ".localhost")
    )


def public_domains(site: "SiteConfig") -> list[str]:
    """Return domains certbot could issue for."""
    return [domain for domain in site.all_domains if is_public_domain(domain)]


def certificate_domains(site: "SiteConfig") -> list[str]:
    """Return public domains whose TLS terminates on this host."""
    return [domain for domain in site.tls_domains if is_public_domain(domain)]


def has_domain_coverage(cert_file: Path, domains: list[str]) -> bool:
    """True if the on-disk cert's SAN list already includes every domain."""
    import subprocess

    result = subprocess.run(
        _privileged(["openssl", "x509", "-noout", "-ext", "subjectAltName", "-in", str(cert_file)]),
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return False
    sans = {token.strip().removeprefix("DNS:") for token in result.stdout.replace(",", " ").split()}
    return all(domain in sans for domain in domains)


def letsencrypt_active(bench: "Bench") -> bool:
    """True if this bench is configured to obtain its own TLS certificates."""
    return bool(bench.config.letsencrypt.email) and bench.config.admin.tls


def letsencrypt_email_required(bench: "Bench") -> bool:
    """True when local TLS would issue a public cert and needs an email."""
    # Local TLS still needs a certificate regardless of admin TLS.
    if any(certificate_domains(site.config) for site in bench.sites()):
        return True
    if not bench.config.admin.tls:
        return False
    # Admin TLS also enables public site TLS in production.
    if any(public_domains(site.config) for site in bench.sites()):
        return True
    return is_public_domain(bench.config.admin.domain)


def is_letsencrypt_required(bench: "Bench") -> bool:
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

    def setup_sudoers(self) -> None:
        """Install the passwordless sudo grant needed by certbot."""
        if self.has_passwordless_sudo:
            return
        bench_user = pwd.getpwuid(self.bench.path.stat().st_uid).pw_name
        certbot = which("certbot") or "/usr/bin/certbot"
        openssl = which("openssl") or "/usr/bin/openssl"
        mkdir = which("mkdir") or "/bin/mkdir"
        test = which("test") or "/usr/bin/test"
        webroot_path = self.bench.config.letsencrypt.webroot_path
        hook = _nginx_reload_hook()
        # Domain/cert-name/email tokens must stay wildcarded (new sites arrive after
        # this grant is installed), but every wildcard here is anchored between fixed
        # literal text on both sides - nothing can smuggle in extra flags (e.g. a
        # different --deploy-hook, or openssl -out) before or after the match.
        install_sudoers_grant(
            self.bench.config_path / "letsencrypt",
            bench_user,
            "certbot",
            [
                # obtain(): multiple -d flags + --cert-name + --expand
                f"{certbot} certonly --webroot -w {webroot_path} * --cert-name * "
                f"--expand --email * --agree-tos --non-interactive --deploy-hook {hook}",
                # obtain_admin(): single -d, no --cert-name/--expand
                f"{certbot} certonly --webroot -w {webroot_path} -d * --email * "
                f"--agree-tos --non-interactive --deploy-hook {hook}",
                f"{certbot} renew --quiet",
                f"{mkdir} -p {webroot_path}",
                # cert_files_exist(): live/ is 0700, so existence checks need privilege
                f"{test} -f {LETSENCRYPT_LIVE}/*/fullchain.pem -a -f {LETSENCRYPT_LIVE}/*/privkey.pem",
                f"{openssl} x509 -noout -ext subjectAltName -in {LETSENCRYPT_LIVE}/*/fullchain.pem",
                f"{openssl} x509 -enddate -noout -in {LETSENCRYPT_LIVE}/*/fullchain.pem",
            ],
        )

    @property
    def has_passwordless_sudo(self) -> bool:
        """Whether the certbot sudoers grant is active."""
        certbot = which("certbot") or "/usr/bin/certbot"
        return has_passwordless_sudo_for([certbot, "renew", "--quiet"])

    def ensure_webroot(self) -> None:
        # /var/www is root-owned, so create the webroot with sudo. Default 0755
        # lets certbot (root) write ACME challenges and nginx read them.
        run_command(_privileged(["mkdir", "-p", str(self.bench.config.letsencrypt.webroot_path)]))

    def obtain(self, site: "SiteConfig") -> None:
        from pilot.managers.nginx import NginxManager

        domains = certificate_domains(site)
        if not domains:
            return  # nothing certbot can validate that this host terminates

        nginx_manager = NginxManager(self.bench)
        if (
            nginx_manager.has_cert(site)
            and not self._is_near_expiry(site)
            and self._has_domain_coverage(nginx_manager.cert_path(site), domains)
        ):
            print(f"Certificate for {site.name} already covers all domains and is not near expiry. Skipping.")
            return

        domain_args = []
        for domain in domains:
            domain_args.extend(["-d", domain])

        webroot_path = str(self.bench.config.letsencrypt.webroot_path)
        email = self.bench.config.letsencrypt.email

        run_command(
            _privileged(
                [
                    "certbot",
                    "certonly",
                    "--webroot",
                    "-w",
                    webroot_path,
                    *domain_args,
                    "--cert-name",
                    self.bench.certificate_name(site),
                    "--expand",
                    "--email",
                    email,
                    "--agree-tos",
                    "--non-interactive",
                    "--deploy-hook",
                    _nginx_reload_hook(),
                ]
            )
        )

    def obtain_all(self) -> None:
        """Issue certificates for domains whose TLS ends on this host."""
        from pilot.exceptions import CommandError

        failed = []
        for site in self.bench.sites():
            if not certificate_domains(site.config):
                continue
            try:
                if site.config.has_released_certificate_pin:
                    self._retire_released_pin(site)
                else:
                    self.obtain(site.config)
            except CommandError as exc:
                domains = ", ".join(f"'{domain}'" for domain in certificate_domains(site.config))
                print(f"Could not obtain a certificate for {domains} (site '{site.config.name}'), skipping: {exc}")
                failed.append(site.config.name)
        if self.bench.config.admin.tls and is_public_domain(self.bench.config.admin.domain):
            try:
                self.obtain_admin()
            except CommandError as exc:
                print(
                    f"Could not obtain a certificate for '{self.bench.config.admin.domain}', skipping: {exc}"
                )
                failed.append(self.bench.config.admin.domain)
        # Don't raise: certs that did issue should still get TLS applied. Domains
        # that failed stay on HTTP and can be retried later.
        if failed:
            print(f"Certificate issuance failed for: {', '.join(failed)}. These stay on HTTP.")

    def _retire_released_pin(self, site: "Site") -> None:
        """Issue under the site's name, then release its old certificate pin."""
        from dataclasses import replace

        self.obtain(replace(site.config, cert_name=""))
        site.clear_certificate_pin()

    def obtain_admin(self) -> None:
        from pilot.managers.nginx import NginxManager

        nginx_manager = NginxManager(self.bench)
        domain = self.bench.config.admin.domain

        if nginx_manager.has_admin_cert and not self._is_near_expiry_cert(nginx_manager.admin_cert_path):
            print(f"Certificate for {domain} already exists and is not near expiry. Skipping.")
            return

        run_command(
            _privileged(
                [
                    "certbot",
                    "certonly",
                    "--webroot",
                    "-w",
                    str(self.bench.config.letsencrypt.webroot_path),
                    "-d",
                    domain,
                    "--email",
                    self.bench.config.letsencrypt.email,
                    "--agree-tos",
                    "--non-interactive",
                    "--deploy-hook",
                    _nginx_reload_hook(),
                ]
            )
        )

    def renew(self) -> None:
        run_command(_privileged(["certbot", "renew", "--quiet"]))

    def _has_domain_coverage(self, cert_file: Path, domains: list[str]) -> bool:
        return has_domain_coverage(cert_file, domains)

    def _is_near_expiry(self, site: "SiteConfig") -> bool:
        from pilot.managers.nginx import NginxManager

        nginx_manager = NginxManager(self.bench)
        return self._is_near_expiry_cert(nginx_manager.cert_path(site))

    def _is_near_expiry_cert(self, cert_file: Path) -> bool:
        import subprocess
        from datetime import datetime

        result = subprocess.run(
            _privileged(["openssl", "x509", "-enddate", "-noout", "-in", str(cert_file)]),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return True

        date_str = result.stdout.strip().replace("notAfter=", "")
        expiry = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
        now = datetime.now(tz=UTC)
        return (expiry - now).days < _CERT_EXPIRY_THRESHOLD_DAYS
