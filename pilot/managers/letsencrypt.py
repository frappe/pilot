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
# certbot renews from 30 days left, so a certificate this close to expiry is failing to renew.
_CERT_ALERT_DAYS = 14
_RENEWAL_TIMER = """\
[Unit]
Description=certificate renewal timer

[Timer]
OnBootSec=15min
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
"""
_RENEWAL_SERVICE = """\
[Unit]
Description=certificate renewal and expiry check

[Service]
Type=oneshot
WorkingDirectory={cli_root}
Environment=PYTHONPATH={cli_root}
ExecStart={python} -m pilot.managers.letsencrypt
StandardOutput=append:{cli_root}/system/logs/certificates.log
StandardError=append:{cli_root}/system/logs/certificates.log

[Install]
WantedBy=default.target
"""


def _nginx_reload_hook() -> str:
    """Return the certbot deploy hook that reloads nginx."""
    return "systemctl reload nginx"


def is_public_domain(domain: str) -> bool:
    """Whether certbot can validate the domain publicly."""
    domain = domain.strip().lower().rstrip(".")
    return (
        bool(domain)
        and domain not in {"local", "localhost"}
        and not domain.endswith((".local", ".localhost"))
    )


def public_domains(site: "SiteConfig") -> list[str]:
    """Return domains certbot could issue for."""
    return [domain for domain in site.all_domains if is_public_domain(domain)]


def certificate_domains(site: "SiteConfig") -> list[str]:
    """Return public domains whose TLS terminates on this host."""
    return [domain for domain in local_public_domains(site) if site.terminates_tls(domain)]


def local_public_domains(site: "SiteConfig") -> list[str]:
    """Public domains eligible for local TLS, respecting provider routing."""
    from pilot.core.adapters.domain_provider import DomainRouteProvider
    from pilot.utils import matches_wildcard

    patterns: list[str] | None = None
    domains = []
    for domain in public_domains(site):
        route = site.configured_route_for(domain)
        if route is not None:
            if route.origin_tls:
                domains.append(domain)
            continue
        if patterns is None:
            patterns = DomainRouteProvider.wildcard_domains()
        if not matches_wildcard(domain, patterns):
            domains.append(domain)
    return domains


def is_admin_certificate_required(bench: "Bench") -> bool:
    """Whether the public Admin hostname needs a certificate on this host."""
    from pilot.core.adapters.domain_provider import DomainRouteProvider
    from pilot.utils import matches_wildcard

    admin = bench.config.admin
    if not admin.tls or not is_public_domain(admin.domain):
        return False
    if admin.route is not None:
        return admin.route.origin_tls
    return not matches_wildcard(admin.domain, DomainRouteProvider.wildcard_domains())


def certificate_domain_names(cert_file: Path) -> set[str]:
    """The DNS names in the on-disk cert's SAN list, or none when it cannot be read."""
    import subprocess

    result = subprocess.run(
        _privileged(["openssl", "x509", "-noout", "-ext", "subjectAltName", "-in", str(cert_file)]),
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return set()
    # The output starts with an "X509v3 Subject Alternative Name:" header line.
    tokens = result.stdout.replace(",", " ").split()
    return {token.removeprefix("DNS:") for token in tokens if token.startswith("DNS:")}


def has_domain_coverage(cert_file: Path, domains: list[str]) -> bool:
    """True if the on-disk cert's SAN list already includes every domain."""
    return set(domains) <= certificate_domain_names(cert_file)


def certificate_days_left(cert_file: Path) -> int | None:
    """Whole days until the on-disk cert expires, or None when it cannot be read."""
    import subprocess
    from datetime import datetime

    result = subprocess.run(
        _privileged(["openssl", "x509", "-enddate", "-noout", "-in", str(cert_file)]),
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        return None
    date_str = result.stdout.strip().replace("notAfter=", "")
    expiry = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
    return (expiry - datetime.now(tz=UTC)).days


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
    if any(local_public_domains(site.config) for site in bench.sites()):
        return True
    return is_admin_certificate_required(bench)


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
            bare_commands=[certbot, mkdir, test, openssl],
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
        # An exact match: a removed domain left in the certificate fails its renewal for every domain.
        if (
            nginx_manager.has_cert(site)
            and not self._is_near_expiry(site)
            and certificate_domain_names(nginx_manager.cert_path(site)) == set(domains)
        ):
            print(f"Certificate for {site.name} matches its domains and is not near expiry. Skipping.")
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
                    "--renew-with-new-domains",
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
                print(
                    f"Could not obtain a certificate for {domains} (site '{site.config.name}'), skipping: {exc}"
                )
                failed.append(site.config.name)
        if is_admin_certificate_required(self.bench):
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

        if not is_admin_certificate_required(self.bench):
            return
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
        """Refresh this bench's current origin certificates without renewing retired routes."""
        from pilot.internal.atomic_file import exclusive_file_lock
        from pilot.managers.nginx import NginxManager
        from pilot.utils import make_private_directory

        directory = self.bench.config_path / "site-renames"
        make_private_directory(directory, parents=True)
        with exclusive_file_lock(directory / "operations"):
            if not any(
                certificate_domains(site.config) for site in self.bench.sites()
            ) and not is_admin_certificate_required(self.bench):
                return
            self.obtain_all()
            NginxManager(self.bench).apply_config()

    def _is_near_expiry(self, site: "SiteConfig") -> bool:
        from pilot.managers.nginx import NginxManager

        nginx_manager = NginxManager(self.bench)
        return self._is_near_expiry_cert(nginx_manager.cert_path(site))

    def _is_near_expiry_cert(self, cert_file: Path) -> bool:
        days_left = certificate_days_left(cert_file)
        return days_left is None or days_left < _CERT_EXPIRY_THRESHOLD_DAYS

    def get_certificate_paths(self) -> list[tuple[str, Path]]:
        """Existing certificate files for this bench's current local TLS routes."""
        from pilot.managers.nginx import NginxManager

        nginx = NginxManager(self.bench)
        certificates = []
        for site in self.bench.sites():
            if certificate_domains(site.config) and nginx.has_cert(site.config):
                certificates.append((self.bench.certificate_name(site.config), nginx.cert_path(site.config)))
        if is_admin_certificate_required(self.bench) and nginx.has_admin_cert:
            certificates.append((self.bench.config.admin.domain, nginx.admin_cert_path))
        return certificates

    def expiring_certificates(self) -> list[tuple[str, int]]:
        """(certificate name, days left) for this bench's certificates close to expiry."""
        expiring = []
        for name, path in self.get_certificate_paths():
            days_left = certificate_days_left(path)
            if days_left is not None and days_left < _CERT_ALERT_DAYS:
                expiring.append((name, days_left))
        return expiring

    def alert_expiring_certificates(self) -> None:
        """Warn in the bench's feed and its alert sinks while a certificate fails to renew."""
        from pilot.core.alerts import notify
        from pilot.core.notification.events import record_alert

        for name, days_left in self.expiring_certificates():
            payload = {
                "event": "certificate_expiry",
                "message": f"The certificate for {name} expires in {days_left} days and has not renewed.",
                "context": {"bench": self.bench.config.name, "certificate": name, "days_left": days_left},
            }
            title = f"Certificate for {name} is not renewing"
            record_alert(self.bench, payload, category="Sites", severity="Warning", title=title)
            notify(self.bench, payload)


def is_renewal_timer_installed() -> bool:
    from pilot.managers.systemd_user import user_timer_installed

    return user_timer_installed("pilot-certificates.timer")


def install_renewal_timer() -> None:
    """Install the host-wide daily timer that renews certificates and warns before one
    expires, once; Fedora and Arch do not enable certbot's timer."""
    from pilot.managers.environment import AdminEnvManager
    from pilot.managers.systemd_user import install_user_timer
    from pilot.utils import cli_root

    if is_renewal_timer_installed():
        return
    root = cli_root()
    (root / "system" / "logs").mkdir(parents=True, exist_ok=True)
    install_user_timer(
        unit_dir=root / "system" / "certificates",
        unit_name="pilot-certificates.service",
        unit_text=_RENEWAL_SERVICE.format(cli_root=root, python=AdminEnvManager(root).python),
        timer_unit_name="pilot-certificates.timer",
        timer_text=_RENEWAL_TIMER,
    )


def main() -> None:
    """Refresh each bench's local certificates, then check their expiry."""
    from pilot.core.bench import Bench
    from pilot.exceptions import BenchError
    from pilot.utils import cli_root, iter_sibling_benches

    benches = [
        Bench(config, path)
        for path, config in iter_sibling_benches(cli_root() / "benches" / ".certificates-placeholder")
        if config.production.enabled and config.letsencrypt.email
    ]
    for bench in benches:
        manager = LetsEncryptManager(bench)
        try:
            manager.renew()
        except (BenchError, OSError, ValueError) as error:
            print(f"Certificate refresh failed for {bench.path}: {error}")
        try:
            manager.alert_expiring_certificates()
        except (BenchError, OSError, ValueError) as error:
            print(f"Certificate expiry check failed for {bench.path}: {error}")


if __name__ == "__main__":
    main()
