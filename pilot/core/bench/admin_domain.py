from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked
from pilot.utils import host_owner, matches_wildcard, normalize_host

if TYPE_CHECKING:
    from pilot.config import RoutePolicy
    from pilot.core.bench import Bench


class ProductionAdminDomain:
    def __init__(self, bench: "Bench", existing_domain: str) -> None:
        self.bench = bench
        self.existing_domain = existing_domain
        self.registered_domain: str | None = None

    def check(self) -> None:
        """Reject domains that are missing, already claimed, or outside the wildcard set."""
        from pilot.core.adapters.domain_provider import DomainRouteProvider

        domain = self.bench.config.admin.domain
        if not domain:
            return  # config validation raises the required-in-prod error with bench context
        owner = host_owner(self.bench.path, domain)
        if owner:
            raise BenchError(f"Admin domain '{domain}' is already used by bench '{owner}'.")
        # Check this bench separately because host_owner only sees siblings.
        if claimed_by := self.bench.site_claiming(domain):
            raise BenchError(
                f"Admin domain '{domain}' conflicts with this bench's own site '{claimed_by}'. "
                f"An admin domain must not match a site domain."
            )
        if normalize_host(domain) == normalize_host(self.existing_domain):
            return
        patterns = DomainRouteProvider.wildcard_domains()
        if patterns and not matches_wildcard(domain, patterns):
            raise BenchError(
                f"Admin domain must match one of this bench's wildcard domains: {', '.join(patterns)}."
            )

    def register(self) -> None:
        """Provision a new/changed admin domain with the route provider."""
        from pilot.core.adapters.domain_provider import DomainRouteProvider

        self.registered_domain = None
        domain = self.bench.config.admin.domain
        if not domain or normalize_host(domain) == normalize_host(self.existing_domain):
            return
        policy = DomainRouteProvider(self.bench).register(domain, domain)
        if policy:
            self.bench.config.admin.route = policy
            self.bench.config.admin.tls = policy.origin_tls
        self.registered_domain = domain

    def rollback(self) -> None:
        """Release the just-registered admin route after a failed setup."""
        if not self.registered_domain:
            return
        from pilot.core.adapters.domain_provider import DomainRouteProvider

        DomainRouteProvider(self.bench).release(self.registered_domain)

    def release_previous(self) -> None:
        """Free the superseded admin hostname once the switch is committed."""
        if not self.registered_domain or not self.existing_domain:
            return
        from pilot.core.adapters.domain_provider import DomainRouteProvider

        DomainRouteProvider(self.bench).release(self.existing_domain)


class AdminDomainChange:
    """Move the admin route, config, nginx host, and TLS together."""

    def __init__(self, bench: "Bench", domain: str, tls: bool | None = None) -> None:
        self.bench = bench
        self.domain = domain.strip().lower()
        self.tls = tls
        self.previous = bench.config.admin.domain
        self._route = ProductionAdminDomain(bench, self.previous)

    def run(self, on_progress: Callable[[str], None] = lambda message: None) -> None:
        admin = self.bench.config.admin
        restore = (admin.domain, admin.tls, admin.route)
        self._validate()

        admin.domain = self.domain
        if self.tls is not None:
            admin.tls = self.tls
        self._route.check()
        self._route.register()

        on_progress(f"Moving the admin to '{self.domain}'...")
        try:
            self._persist()
            self._retarget_hostname_aliases()
            self._republish_nginx()
            if self._reissue_certificate(on_progress):
                self._republish_nginx()
            self._require_tls_if_requested()
            self.repoint_pilot_endpoints()
        except BaseException:
            self._roll_back(restore, on_progress)
            raise

        self._route.release_previous()
        self._clear_site_cache(on_progress)
        on_progress(f"\nAdmin is now at {self._served_scheme()}://{self.domain}")

    def _clear_site_cache(self, on_progress: Callable[[str], None]) -> None:
        """Drop cached site configs so sites read the new pilot_endpoint."""
        try:
            self.bench.clear_cache()
        except (BenchError, OSError) as exc:
            on_progress(f"Warning: could not clear the site cache: {exc}")

    def _served_scheme(self) -> str:
        admin = self.bench.config.admin
        if admin.route:
            return admin.route.public_scheme
        from pilot.managers.nginx import NginxManager

        return "https" if admin.tls and NginxManager(self.bench).has_admin_cert else "http"

    def _validate(self) -> None:
        from pilot.internal.validators import validate_hostname

        if not self.domain:
            raise BenchError("An admin domain is required.")
        if error := validate_hostname(self.domain, "admin domain"):
            raise BenchError(error)
        if normalize_host(self.domain) == normalize_host(self.previous) and self.tls is None:
            raise BenchError(f"The admin domain is already '{self.domain}'.")
        route = self.bench.config.admin.route
        if (
            route
            and normalize_host(self.domain) == normalize_host(self.previous)
            and self.tls is not None
            and self.tls != route.origin_tls
        ):
            raise BenchError("The domain provider controls TLS for the admin domain.")

    def _persist(self) -> None:
        from pilot.config import BenchConfig

        admin = self.bench.config.admin
        with BenchConfig.open(self.bench.path, mode="raw") as data:
            values = {"domain": admin.domain, "tls": admin.tls}
            if admin.route:
                values["route"] = admin.route.to_dict()
            else:
                data.setdefault("admin", {}).pop("route", None)
            data.setdefault("admin", {}).update(values)

    def repoint_pilot_endpoints(self) -> None:
        """Point every existing `pilot_endpoint` in the bench's site configs at the admin URL."""
        endpoint = self.bench.admin_endpoint
        common_config_path = self.bench.sites_path / "common_site_config.json"
        self._repoint_pilot_endpoint(common_config_path, endpoint, indent=2)
        for config_path in sorted(self.bench.sites_path.glob("*/site_config.json")):
            if not config_path.parent.is_symlink():
                self._repoint_pilot_endpoint(config_path, endpoint, indent=1)

    def _repoint_pilot_endpoint(self, config_path: Path, endpoint: str, indent: int) -> None:
        if not config_path.is_file() or config_path.is_symlink():
            return
        with exclusive_file_lock(config_path):
            config = json.loads(config_path.read_text())
            if "pilot_endpoint" not in config or config["pilot_endpoint"] == endpoint:
                return
            config["pilot_endpoint"] = endpoint
            replace_private_text_locked(config_path, json.dumps(config, indent=indent))

    def _retarget_hostname_aliases(self) -> None:
        self.bench.hostname_aliases.retarget("admin", self.previous, self.domain)

    def _reissue_certificate(self, on_progress: Callable[[str], None]) -> bool:
        """Try to obtain the new certificate; pending DNS leaves HTTP active."""
        from pilot.managers.letsencrypt import LetsEncryptManager, letsencrypt_active
        from pilot.managers.nginx import NginxManager

        if not self.bench.config.admin.tls or not letsencrypt_active(self.bench):
            return False
        on_progress(f"Obtaining a certificate for {self.domain}...")
        try:
            # obtain_admin also renews an expired certificate.
            LetsEncryptManager(self.bench).obtain_admin()
        except Exception as exc:
            on_progress(
                f"Could not obtain a certificate for {self.domain} yet ({exc}). "
                f"Serving the admin over HTTP - retry once its DNS resolves."
            )
            return False
        return NginxManager(self.bench).has_admin_cert

    def _require_tls_if_requested(self) -> None:
        """Refuse to finish a TLS move that has no certificate to serve.

        Left to stand, the admin would answer only on HTTP while `admin.tls`
        stayed true - so session cookies keep their Secure flag and no browser
        sends them back - and the previous hostname would already have been
        released. Failing here rolls the move back instead, leaving the admin
        where it still works.
        """
        from pilot.managers.nginx import NginxManager

        if not self.bench.config.admin.tls or NginxManager(self.bench).has_admin_cert:
            return
        raise BenchError(
            f"No TLS certificate for '{self.domain}', so the admin would serve HTTP while "
            f"configured for HTTPS. The admin is unchanged; retry once its DNS resolves, or "
            f"pass tls=false to move it to plain HTTP."
        )

    def _roll_back(
        self,
        restore: tuple[str, bool, "RoutePolicy | None"],
        on_progress: Callable[[str], None],
    ) -> None:
        """Undo committed config and routes after a failed switch."""
        admin = self.bench.config.admin
        admin.domain, admin.tls, admin.route = restore
        with contextlib.suppress(Exception):
            self._persist()
        with contextlib.suppress(Exception):
            self.bench.hostname_aliases.retarget("admin", self.domain, self.previous)
        with contextlib.suppress(Exception):
            self.repoint_pilot_endpoints()
        # Rollback must not hide the original failure or skip nginx recovery.
        with contextlib.suppress(Exception):
            self._route.rollback()
        with contextlib.suppress(Exception):
            self._republish_nginx()
        on_progress(f"Rolled the admin back to '{self.previous}'.")

    def _republish_nginx(self) -> None:
        from pilot.managers.nginx import NginxManager

        if not self.bench.config.production.enabled:
            return
        nginx = NginxManager(self.bench)
        nginx.generate_config(ssl_ready=True)
        nginx.reload()
