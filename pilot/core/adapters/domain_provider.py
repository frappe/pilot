from __future__ import annotations

import json
import socket
import subprocess
import sys
import urllib.request
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError, DomainConflictError, DomainProviderError
from pilot.utils import write_private_text
from pilot.managers.platform import which
from pilot.utils import host_owner, normalize_host

if TYPE_CHECKING:
    from pilot.core.bench import Bench

# Optional managed-hosting hook. Failures surface stderr as the user error.
# Data verbs return one JSON value on stdout; register/deregister stdout is ignored.
_PROVIDER_BIN = "bench-domain-provider"


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

    def register(self, site_name: str, domain: str) -> None:
        """Attach a domain locally, delegating DNS/routing checks when available."""
        domain = normalize_host(domain)
        if domain == normalize_host(site_name):
            self._ask_provider("register", domain)
            return
        domain = self._validate_new(site_name, domain)
        ran, _ = self._ask_provider("register", domain)
        if not ran:
            self._verify(site_name, domain)
        config = self._read(site_name)
        config.setdefault("domains", []).append(domain)
        self._write(site_name, config)

    def deregister(self, site_name: str, domain: str) -> None:
        """Detach a non-primary domain and notify the provider first."""
        domain = normalize_host(domain)
        if domain == normalize_host(site_name):
            self._ask_provider("deregister", domain)
            return
        primary = self.primary(site_name)
        if primary and normalize_host(primary) == domain:
            raise DomainConflictError(
                "Cannot remove the primary domain. Make another domain primary first."
            )
        self._ask_provider("deregister", domain)
        config = self._read(site_name)
        config["domains"] = [d for d in (config.get("domains") or []) if normalize_host(self._name(d)) != domain]
        self._write(site_name, config)

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
        return DomainRouteProvider._host_query("proxy-servers")

    @staticmethod
    def _host_query(verb: str) -> list[str]:
        """Run a host-level provider verb that returns a JSON list."""
        exe = which(_PROVIDER_BIN)
        if not exe:
            return []
        result = subprocess.run([exe, verb], capture_output=True, text=True)
        if result.returncode != 0:
            raise DomainProviderError(
                result.stderr.strip() or f"{_PROVIDER_BIN} {verb} failed."
            )
        out = result.stdout.strip()
        return json.loads(out) if out else []

    def domains(self, site_name: str) -> list[str]:
        return self._names(self._read(site_name))

    def primary(self, site_name: str) -> str | None:
        host = (self._read(site_name).get("host_name") or "").strip()
        return host.split("://", 1)[-1] or None if host else None

    def set_primary(self, site_name: str, domain: str | None) -> None:
        config = self._read(site_name)
        if not domain:
            config.pop("host_name", None)
            self._write(site_name, config)
            return
        domain = normalize_host(domain)
        candidates = {normalize_host(site_name)} | {normalize_host(d) for d in self._names(config)}
        if domain not in candidates:
            raise DomainConflictError(f"{domain} is not a domain of this site.")
        scheme = "https" if config.get("ssl") else "http"
        config["host_name"] = f"{scheme}://{domain}"
        self._write(site_name, config)

    def _config_path(self, site_name: str):
        return self.bench.sites_path / site_name / "site_config.json"

    def _read(self, site_name: str) -> dict:
        path = self._config_path(site_name)
        if not path.exists():
            raise BenchError(f"Site '{site_name}' not found.")
        return json.loads(path.read_text())

    def _write(self, site_name: str, config: dict) -> None:
        write_private_text(self._config_path(site_name), json.dumps(config, indent=1))

    def _ask_provider(self, action: str, domain: str | None = None, *, site: str | None = None) -> tuple[bool, object]:
        """Run bench-domain-provider when installed."""
        exe = which(_PROVIDER_BIN)
        if not exe:
            return False, None
        argv = [exe, action, *([site] if site else []), *([domain] if domain else [])]
        result = subprocess.run(argv, capture_output=True, text=True)
        if result.returncode != 0:
            raise DomainProviderError(
                result.stderr.strip() or f"{_PROVIDER_BIN} {action} failed."
            )
        # Do not parse status text from mutating verbs.
        if action in ("register", "deregister"):
            return True, None
        out = result.stdout.strip()
        return True, (json.loads(out) if out else None)

    def _validate_new(self, site_name: str, domain: str) -> str:
        """Normalize domain and reject duplicates across sibling benches."""
        domain = normalize_host(domain)
        if not domain:
            raise DomainConflictError("A domain is required.")
        if domain == normalize_host(self.bench.config.admin.domain):
            raise DomainConflictError(
                f"{domain} is already used by this bench's admin domain."
            )
        for site in self.bench.sites():
            if domain in (normalize_host(d) for d in site.config.all_domains):
                if site.config.name == site_name:
                    raise DomainConflictError(
                        f"{domain} is already attached to this site."
                    )
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
        """Raise unless the domain resolves to this server."""
        candidate = self._resolve(domain)
        if not candidate:
            raise DomainConflictError(
                f"{domain} doesn't resolve yet. Retry once DNS has updated."
            )
        expected = self._resolve(site_name)
        if ip := self._server_ip():
            expected.add(ip)
        if not (candidate & expected):
            raise DomainConflictError(
                f"{domain} doesn't point here yet. Retry once DNS has updated."
            )

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
