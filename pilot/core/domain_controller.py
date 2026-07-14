from __future__ import annotations

import json
import socket
import subprocess
import sys
import urllib.request
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.platform import which
from pilot.utils import host_owner, normalize_host

if TYPE_CHECKING:
    from pilot.core.bench import Bench

# Optional cloud/managed-hosting extension that takes over routing entirely when present.
#
# Contract for `bench-domain-provider`, invoked as:
#   bench-domain-provider generate-dns-records <site> <domain>
#   bench-domain-provider register <domain>
#   bench-domain-provider deregister <domain>
#   bench-domain-provider wildcard-domains
#   bench-domain-provider proxy-servers
#
# - Only generate-dns-records needs the site (its first positional arg, for the
#   CNAME target); the rest operate on the domain alone. The process inherits the
#   caller's environment.
# - On success (exit code 0), stdout is either empty or a single JSON value:
#     generate-dns-records -> {"cname": [...], "a": [...]} — two sets of
#                              {"type", "host", "value"} records, one per validation
#                              method; either may be empty, or {} / blank if the
#                              domain needs no DNS records at all.
#     register, deregister -> output ignored; blank stdout is fine.
#     wildcard-domains     -> a JSON list of domain strings, or blank for none.
#     proxy-servers        -> a JSON list of edge-proxy IPs that front this bench,
#                              or blank for none (the bench is directly exposed).
# - On failure, exit non-zero and write a human-readable error to stderr; that
#   text becomes the BenchError message shown to the user. stdout is ignored.
# - On failure, exit non-zero and write a human-readable error to stderr; that
#   text becomes the BenchError message shown to the user. stdout is ignored.
_PROVIDER_BIN = "bench-domain-provider"


class DomainRouteProvider:
    """Custom domains for sites in a bench: DNS records, attach/detach, and primary domain."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def generate_dns_records(self, site_name: str, domain: str) -> dict:
        """Step 1 of attaching a domain: validate it's free, return {"cname": [...], "a": [...]}
        record sets — one per validation method; either may be empty if that route isn't an option.

        The local basic checks (free on this bench, not the admin domain) run even when a
        provider handles the records — the provider layers its own global validation on top."""
        self._read(site_name)
        domain = self._validate_new(site_name, domain)
        ran, data = self._ask_provider("generate-dns-records", domain, site=site_name)
        if ran:
            return data or {}
        records = {"cname": [{"type": "CNAME", "host": domain, "value": site_name}], "a": []}
        if ip := self._server_ip():
            records["a"] = [{"type": "A", "host": domain, "value": ip}]
        return records

    def register(self, site_name: str, domain: str) -> None:
        """Step 2: validate the domain is free (always, so a provider can't
        register a hostname already claimed in this or a sibling bench), verify
        DNS unless a provider handled it, then persist. The site's own name needs
        no domains-list entry, so it returns early without persisting."""
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
        """Detach domain unless it's the primary. Always persists; the provider
        (if any) is just told about it first, so a failure there stops us early.
        The site's own name has no domains-list entry, so it returns early after
        telling the provider, mirroring register."""
        domain = normalize_host(domain)
        if domain == normalize_host(site_name):
            self._ask_provider("deregister", domain)
            return
        primary = self.primary(site_name)
        if primary and normalize_host(primary) == domain:
            raise BenchError("Cannot remove the primary domain. Make another domain primary first.")
        self._ask_provider("deregister", domain)
        config = self._read(site_name)
        config["domains"] = [d for d in (config.get("domains") or []) if normalize_host(self._name(d)) != domain]
        self._write(site_name, config)

    def release(self, domain: str) -> None:
        """Tell the provider to drop a route without touching local config — for
        teardown after the site directory is already gone. Best effort: a provider
        failure is swallowed so it can't fail an otherwise-successful drop."""
        try:
            self._ask_provider("deregister", normalize_host(domain))
        except BenchError as exc:
            print(f"Warning: provider could not release '{domain}': {exc}", file=sys.stderr)

    @staticmethod
    def wildcard_domains() -> list[str]:
        """Wildcard domain patterns (e.g. '*.example.com') the provider extension
        offers, or [] if none. Host-level — no bench/site needs to exist yet."""
        return DomainRouteProvider._host_query("wildcard-domains")

    @staticmethod
    def proxy_servers() -> list[str]:
        """IPs of the edge proxies the provider extension puts in front of this
        bench, or [] if none/not installed. Host-level — no bench/site in scope."""
        return DomainRouteProvider._host_query("proxy-servers")

    @staticmethod
    def _host_query(verb: str) -> list[str]:
        """Run a host-level provider verb that returns a JSON list, or [] if the
        provider isn't installed or emits nothing."""
        exe = which(_PROVIDER_BIN)
        if not exe:
            return []
        result = subprocess.run([exe, verb], capture_output=True, text=True)
        if result.returncode != 0:
            raise BenchError(result.stderr.strip() or f"{_PROVIDER_BIN} {verb} failed.")
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
            raise BenchError(f"{domain} is not a domain of this site.")
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
        self._config_path(site_name).write_text(json.dumps(config, indent=1))

    def _ask_provider(self, action: str, domain: str | None = None, *, site: str | None = None) -> tuple[bool, object]:
        """Run `bench-domain-provider <action> [site] [domain]` if installed; (True, JSON or None) if it ran, else (False, None)."""
        exe = which(_PROVIDER_BIN)
        if not exe:
            return False, None
        argv = [exe, action, *([site] if site else []), *([domain] if domain else [])]
        result = subprocess.run(argv, capture_output=True, text=True)
        if result.returncode != 0:
            raise BenchError(result.stderr.strip() or f"{_PROVIDER_BIN} {action} failed.")
        # Only data-returning actions emit JSON; register/deregister stdout is
        # ignored, so don't parse it (a status line would wrongly fail an applied route).
        if action in ("register", "deregister"):
            return True, None
        out = result.stdout.strip()
        return True, (json.loads(out) if out else None)

    def _validate_new(self, site_name: str, domain: str) -> str:
        """Normalize domain and raise if it's already claimed in this bench or a sibling bench."""
        domain = normalize_host(domain)
        if not domain:
            raise BenchError("A domain is required.")
        if domain == normalize_host(self.bench.config.admin.domain):
            raise BenchError(f"{domain} is already used by this bench's admin domain.")
        for site in self.bench.sites():
            if domain in (normalize_host(d) for d in site.config.all_domains):
                if site.config.name == site_name:
                    raise BenchError(f"{domain} is already attached to this site.")
                raise BenchError(f"{domain} is already used by site '{site.config.name}' in this bench.")
        owner = host_owner(self.bench.path, domain)
        if owner:
            raise BenchError(f"{domain} is already used by bench '{owner}'. Hostnames must be unique across benches.")
        return domain

    def _verify(self, site_name: str, domain: str) -> None:
        """Raise unless domain resolves to this server (CNAME to the site, or matching A record)."""
        candidate = self._resolve(domain)
        if not candidate:
            raise BenchError(f"{domain} doesn't resolve yet. Retry once DNS has updated.")
        expected = self._resolve(site_name)
        if ip := self._server_ip():
            expected.add(ip)
        if not (candidate & expected):
            raise BenchError(f"{domain} doesn't point here yet. Retry once DNS has updated.")

    @staticmethod
    def _server_ip() -> str:
        """Best-effort public IP of this server, for the A-record option."""
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
            return {info[4][0] for info in socket.getaddrinfo(host, None)}
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
