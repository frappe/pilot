from __future__ import annotations

import contextlib
import ipaddress
import json
import socket
import subprocess
import sys
import urllib.request
from collections.abc import Iterator
from typing import TYPE_CHECKING

from pilot.config.proxy import is_cloudflare_address
from pilot.config.route import RoutePolicy
from pilot.exceptions import BenchError, DomainConflictError, DomainProviderError
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked
from pilot.managers.platform import which
from pilot.utils import host_owner, normalize_host

if TYPE_CHECKING:
    from pilot.core.bench import Bench

# Optional managed-hosting hook. Failures surface stderr as the user error.
# Data verbs return one JSON value on stdout; register/deregister stdout is ignored.
_PROVIDER_BIN = "bench-domain-provider"
_PROVIDER_TIMEOUT_SECONDS = 30


def _run_provider(argv: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=_PROVIDER_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as error:
        raise DomainProviderError(
            f"{_PROVIDER_BIN} {argv[1]} did not answer within {_PROVIDER_TIMEOUT_SECONDS} seconds."
        ) from error


class DomainRouteProvider:
    """Custom domains for sites in a bench: DNS records, attach/detach, and primary domain."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def generate_dns_records(self, site_name: str, domain: str) -> dict:
        """Validate a domain and return DNS record options for it."""
        self._read(site_name)
        domain = self._validate_new(site_name, domain)
        ran, data = self._ask_provider("generate-dns-records", domain, site=site_name)
        if ran:
            return data if isinstance(data, dict) else {}
        records = {"cname": [{"type": "CNAME", "host": domain, "value": site_name}], "a": []}
        if ip := self._server_ip():
            records["a"] = [{"type": "A", "host": domain, "value": ip}]
        return records

    def register(self, site_name: str, domain: str) -> RoutePolicy | None:
        """Attach a domain locally, delegating DNS/routing checks when available."""
        domain = normalize_host(domain)
        if domain == normalize_host(site_name):
            ran, data = self._register_with_provider(domain)
            return self._route_policy(domain, ran, data)
        domain = self._validate_new(site_name, domain)
        ran, data = self._register_with_provider(domain)
        if not ran:
            self._verify(site_name, domain)
        policy = self._route_policy(domain, ran, data)
        entry = {"domain": domain, "route": policy.to_dict()} if policy else domain
        with self._edit_config(site_name) as config:
            config.setdefault("domains", []).append(entry)
        return policy

    def _register_with_provider(self, domain: str) -> tuple[bool, object]:
        try:
            return self._ask_provider("register", domain)
        except (ValueError, TypeError) as error:
            with contextlib.suppress(BenchError):
                self._ask_provider("deregister", domain)
            raise DomainProviderError(f"The domain provider returned invalid JSON: {error}") from error

    def deregister(self, site_name: str, domain: str) -> None:
        """Detach a non-primary domain and notify the provider first."""
        domain = normalize_host(domain)
        if domain == normalize_host(site_name):
            self._ask_provider("deregister", domain)
            return
        primary = self.primary(site_name)
        if primary and normalize_host(primary) == domain:
            raise DomainConflictError("Cannot remove the primary domain. Make another domain primary first.")
        self._ask_provider("deregister", domain)
        with self._edit_config(site_name) as config:
            config["domains"] = [
                d for d in (config.get("domains") or []) if normalize_host(self._name(d)) != domain
            ]

    def release(self, domain: str) -> None:
        """Best-effort provider route release after local site teardown."""
        try:
            self._ask_provider("deregister", normalize_host(domain))
        except BenchError as exc:
            print(f"Warning: provider could not release '{domain}': {exc}", file=sys.stderr)

    @staticmethod
    def wildcard_domains() -> list[str]:
        """Wildcard patterns offered by the host-level provider, if installed."""
        return DomainRouteProvider._host_query("wildcard-domains")

    @staticmethod
    def proxy_servers() -> list[str]:
        """Edge-proxy IPs reported by the host-level provider, if installed."""
        servers = DomainRouteProvider._host_query("proxy-servers")
        try:
            for server in servers:
                ipaddress.ip_network(server, strict=False)
        except (TypeError, ValueError) as error:
            raise DomainProviderError(
                "The domain provider returned an invalid proxy address or network."
            ) from error
        return servers

    @staticmethod
    def _host_query(verb: str) -> list[str]:
        """Run a host-level provider verb that returns a JSON list."""
        exe = which(_PROVIDER_BIN)
        if not exe:
            return []
        result = _run_provider([exe, verb])
        if result.returncode != 0:
            raise DomainProviderError(result.stderr.strip() or f"{_PROVIDER_BIN} {verb} failed.")
        out = result.stdout.strip()
        return json.loads(out) if out else []

    def domains(self, site_name: str) -> list[str]:
        return self._names(self._read(site_name))

    def primary(self, site_name: str) -> str | None:
        host = (self._read(site_name).get("host_name") or "").strip()
        return host.split("://", 1)[-1] or None if host else None

    def set_primary(self, site_name: str, domain: str | None) -> None:
        from pilot.config import RoutePolicy, SiteConfig

        with self._edit_config(site_name) as config:
            if not domain:
                config.pop("host_name", None)
                return
            domain = normalize_host(domain)
            candidates = {normalize_host(site_name)} | {normalize_host(d) for d in self._names(config)}
            if domain not in candidates:
                raise DomainConflictError(f"{domain} is not a domain of this site.")

            site = SiteConfig(
                name=site_name,
                apps=[],
                ssl=bool(config.get("ssl")),
                domains=config.get("domains") or [],
                route=RoutePolicy.from_dict(config["route"]) if config.get("route") else None,
            )
            scheme = site.route_for(domain).public_scheme
            config["host_name"] = f"{scheme}://{domain}"

    def _config_path(self, site_name: str):
        return self.bench.sites_path / site_name / "site_config.json"

    def _read(self, site_name: str) -> dict:
        path = self._config_path(site_name)
        if not path.exists():
            raise BenchError(f"Site '{site_name}' not found.")
        return json.loads(path.read_text())

    @contextlib.contextmanager
    def _edit_config(self, site_name: str) -> Iterator[dict]:
        """Read-modify-write site_config.json under its lock; an exception writes nothing."""
        path = self._config_path(site_name)
        with exclusive_file_lock(path):
            config = self._read(site_name)
            yield config
            replace_private_text_locked(path, json.dumps(config, indent=1))

    def _ask_provider(
        self, action: str, domain: str | None = None, *, site: str | None = None
    ) -> tuple[bool, object]:
        """Run bench-domain-provider when installed."""
        exe = which(_PROVIDER_BIN)
        if not exe:
            return False, None
        argv = [exe, action, *([site] if site else []), *([domain] if domain else [])]
        result = _run_provider(argv)
        if result.returncode == 2:
            raise DomainConflictError(result.stderr.strip() or f"{domain or site} was declined.")
        if result.returncode != 0:
            raise DomainProviderError(result.stderr.strip() or f"{_PROVIDER_BIN} {action} failed.")
        if action == "deregister":
            return True, None
        out = result.stdout.strip()
        return True, (json.loads(out) if out else None)

    def _route_policy(self, domain: str, ran: bool, data: object) -> RoutePolicy | None:
        if not ran:
            return None
        try:
            policy = RoutePolicy.from_dict(data)
            if policy.client_ip_source != "direct" and not self.proxy_servers():
                raise DomainProviderError("The domain provider returned a proxy route without proxy servers.")
        except (ValueError, TypeError, BenchError) as error:
            with contextlib.suppress(BenchError):
                self._ask_provider("deregister", domain)
            raise DomainProviderError(
                f"The domain provider returned an invalid route policy: {error}"
            ) from error
        return policy

    def _validate_new(self, site_name: str, domain: str) -> str:
        """Normalize domain and reject duplicates across sibling benches."""
        domain = normalize_host(domain)
        if not domain:
            raise DomainConflictError("A domain is required.")
        if domain == normalize_host(self.bench.config.admin.domain):
            raise DomainConflictError(f"{domain} is already used by this bench's admin domain.")
        for site in self.bench.sites():
            if domain in (normalize_host(d) for d in site.config.all_domains):
                if site.config.name == site_name:
                    raise DomainConflictError(f"{domain} is already attached to this site.")
                raise DomainConflictError(
                    f"{domain} is already used by site '{site.config.name}' in this bench."
                )
        owner = host_owner(self.bench.path, domain)
        if owner:
            raise DomainConflictError(
                f"{domain} is already used by bench '{owner}'. Hostnames must be unique across benches."
            )
        return domain

    def _verify(self, site_name: str, domain: str) -> None:
        """Raise unless the domain resolves to this server or to Cloudflare's proxy."""
        candidate = self._resolve(domain)
        if not candidate:
            raise DomainConflictError(f"{domain} doesn't resolve yet. Retry once DNS has updated.")
        expected = self._resolve(site_name)
        if ip := self._server_ip():
            expected.add(ip)
        if candidate & expected:
            return

        if all(is_cloudflare_address(address) for address in candidate):
            print(
                f"Warning: {domain} resolves to Cloudflare's proxy, so Pilot cannot check that it "
                "points here. Use the Full (strict) SSL mode in Cloudflare.",
                file=sys.stderr,
            )
            return
        raise DomainConflictError(f"{domain} doesn't point here yet. Retry once DNS has updated.")

    @staticmethod
    def _server_ip() -> str:
        """Best-effort public IP for A-record suggestions."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
            if not ip.startswith(("10.", "172.", "192.168.", "127.")):
                return ip
        except OSError:
            pass

        try:
            with urllib.request.urlopen("https://ifconfig.me/ip", timeout=3) as resp:
                return resp.read().decode().strip()
        except OSError:
            return ""

    @staticmethod
    def _resolve(host: str) -> set[str]:
        try:
            return {str(info[4][0]) for info in socket.getaddrinfo(host, None)}
        except OSError:
            return set()

    @staticmethod
    def _names(config: dict) -> list[str]:
        out = []
        for entry in config.get("domains") or []:
            name = entry.get("domain") if isinstance(entry, dict) else entry
            if isinstance(name, str) and name:
                out.append(name)
        return out

    @staticmethod
    def _name(entry) -> str:
        return entry.get("domain", "") if isinstance(entry, dict) else str(entry)
