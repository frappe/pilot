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

_CERT_EXPIRY_THRESHOLD_DAYS = 30


def _nginx_reload_hook() -> str:
    """Shell command certbot runs after a successful (re)issue to pick up the new cert."""
    return "systemctl reload nginx"


def _is_public_domain(domain: str) -> bool:
    """A domain certbot can actually validate over the public internet.
    Local dev domains (``*.localhost``) are excluded."""
    return bool(domain) and not domain.endswith(".localhost")


def public_domains(site: "SiteConfig") -> list[str]:
    """The site's domains certbot can issue for - the only ones a cert covers, so
    a site with an internal name but a public custom domain still gets TLS."""
    return [domain for domain in site.all_domains if _is_public_domain(domain)]


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
    if not bench.config.admin.tls:
        return False
    if any(site.config.ssl and _is_public_domain(site.config.name) for site in bench.sites()):
        return True
    return _is_public_domain(bench.config.admin.domain)


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
        """Give certbot passwordless sudo for exactly the commands issuing and
        renewing certs needs. Idempotent: same deterministic content every call."""
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
        """True when the sudoers grant from `setup_sudoers` lets this user run
        certbot without a password prompt."""
        certbot = which("certbot") or "/usr/bin/certbot"
        return has_passwordless_sudo_for([certbot, "renew", "--quiet"])

    def ensure_webroot(self) -> None:
        # /var/www is root-owned, so create the webroot with sudo. Default 0755
        # lets certbot (root) write ACME challenges and nginx read them.
        run_command(_privileged(["mkdir", "-p", str(self.bench.config.letsencrypt.webroot_path)]))

    def obtain(self, site: "SiteConfig") -> None:
        from pilot.managers.nginx import NginxManager

        domains = public_domains(site)
        if not domains:
            return  # nothing certbot can validate over the public internet

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
                    site.name,
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
        # With TLS disabled a central proxy fronts the bench; obtain nothing.
        if not self.bench.config.admin.tls:
            return
        from pilot.exceptions import CommandError

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
                print(
                    f"Could not obtain a certificate for '{self.bench.config.admin.domain}', skipping: {exc}"
                )
                failed.append(self.bench.config.admin.domain)
        # Don't raise: certs that did issue should still get TLS applied. Domains
        # that failed stay on HTTP and can be retried later.
        if failed:
            print(f"Certificate issuance failed for: {', '.join(failed)}. These stay on HTTP.")

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
