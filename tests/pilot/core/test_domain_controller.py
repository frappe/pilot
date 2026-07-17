"""Tests for DomainRouteProvider driving a real (dummy) bench-domain-provider."""
import json
import os
from pathlib import Path

import pytest

from pilot.config.bench import BenchConfig
from pilot.config.site import SiteConfig
from pilot.core.bench import Bench
from pilot.core.domains import DomainRouteProvider
from pilot.exceptions import BenchError
from pilot.managers.nginx import NginxConfigRenderer


_BENCH_DATA: dict = {
    "bench": {"name": "test-bench", "python": "3.14"},
    "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
    "mariadb": {"root_password": "root"},
    "redis": {"cache_port": 13000, "queue_port": 11000},
}

# Dummy bench-domain-provider: logs its argv to $PROVIDER_LOG and answers each
# verb with canned JSON. $PROVIDER_FAIL=<verb> makes that verb exit non-zero.
_DUMMY_PROVIDER = """#!/usr/bin/env python3
import json, os, sys
argv = sys.argv[1:]
verb = argv[0] if argv else ""
log = os.environ.get("PROVIDER_LOG")
if log:
    open(log, "a").write(" ".join(argv) + "\\n")
if os.environ.get("PROVIDER_FAIL") == verb:
    sys.stderr.write(verb + " declined")
    sys.exit(2)
if verb == "generate-dns-records":
    print(json.dumps({"cname": [{"type": "CNAME", "host": argv[-1], "value": "edge.example.com"}], "a": []}))
elif verb == "wildcard-domains":
    print(json.dumps(["*.example.com"]))
elif verb == "proxy-servers":
    print(json.dumps(["203.0.113.10", "203.0.113.11"]))
sys.exit(0)
"""


def _install_provider(tmp_path: Path, monkeypatch, body: str = _DUMMY_PROVIDER) -> Path:
    """Put an executable bench-domain-provider on PATH; return its argv log path."""
    exe = tmp_path / "bin" / "bench-domain-provider"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(body)
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(exe.parent) + os.pathsep + os.environ["PATH"])
    log = tmp_path / "calls.log"
    monkeypatch.setenv("PROVIDER_LOG", str(log))
    return log


def _make_bench(tmp_path: Path) -> Bench:
    return Bench(BenchConfig._from_dict(_BENCH_DATA), tmp_path)


def _write_site(bench: Bench, name: str, config: dict | None = None) -> None:
    site_dir = bench.sites_path / name
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site_config.json").write_text(json.dumps(config or {}))


def _calls(log: Path) -> list[str]:
    return log.read_text().splitlines()


# --- provider installed -------------------------------------------------------

def test_generate_dns_records_returns_provider_output(tmp_path: Path, monkeypatch) -> None:
    _install_provider(tmp_path, monkeypatch)
    bench = _make_bench(tmp_path)
    _write_site(bench, "mysite")

    records = DomainRouteProvider(bench).generate_dns_records("mysite", "app.example.com")

    assert records == {"cname": [{"type": "CNAME", "host": "app.example.com", "value": "edge.example.com"}], "a": []}


def test_generate_dns_records_passes_site_then_domain(tmp_path: Path, monkeypatch) -> None:
    log = _install_provider(tmp_path, monkeypatch)
    bench = _make_bench(tmp_path)
    _write_site(bench, "mysite")

    DomainRouteProvider(bench).generate_dns_records("mysite", "app.example.com")

    assert _calls(log) == ["generate-dns-records mysite app.example.com"]


def test_generate_dns_records_validates_locally_before_provider(tmp_path: Path, monkeypatch) -> None:
    """The local basic checks run even with a provider installed: a domain already
    taken by a sibling site is rejected here, before the provider is ever called."""
    log = _install_provider(tmp_path, monkeypatch)
    bench = _make_bench(tmp_path)
    _write_site(bench, "mysite")
    _write_site(bench, "other", {"domains": ["app.example.com"]})

    with pytest.raises(BenchError, match="already used by site 'other'"):
        DomainRouteProvider(bench).generate_dns_records("mysite", "app.example.com")
    assert not log.exists()


def test_register_passes_domain_only_and_persists(tmp_path: Path, monkeypatch) -> None:
    log = _install_provider(tmp_path, monkeypatch)
    bench = _make_bench(tmp_path)
    _write_site(bench, "mysite")

    DomainRouteProvider(bench).register("mysite", "app.example.com")

    assert _calls(log) == ["register app.example.com"]
    saved = json.loads((bench.sites_path / "mysite" / "site_config.json").read_text())
    assert saved["domains"] == ["app.example.com"]


def test_deregister_passes_domain_only_and_persists(tmp_path: Path, monkeypatch) -> None:
    log = _install_provider(tmp_path, monkeypatch)
    bench = _make_bench(tmp_path)
    _write_site(bench, "mysite", {"domains": ["app.example.com"]})

    DomainRouteProvider(bench).deregister("mysite", "app.example.com")

    assert _calls(log) == ["deregister app.example.com"]
    saved = json.loads((bench.sites_path / "mysite" / "site_config.json").read_text())
    assert saved["domains"] == []


def test_wildcard_domains_parses_provider_list(tmp_path: Path, monkeypatch) -> None:
    _install_provider(tmp_path, monkeypatch)
    assert DomainRouteProvider.wildcard_domains() == ["*.example.com"]


def test_proxy_servers_parses_provider_list(tmp_path: Path, monkeypatch) -> None:
    _install_provider(tmp_path, monkeypatch)
    assert DomainRouteProvider.proxy_servers() == ["203.0.113.10", "203.0.113.11"]


def test_provider_failure_raises_with_stderr(tmp_path: Path, monkeypatch) -> None:
    _install_provider(tmp_path, monkeypatch)
    monkeypatch.setenv("PROVIDER_FAIL", "proxy-servers")

    with pytest.raises(BenchError, match="proxy-servers declined"):
        DomainRouteProvider.proxy_servers()


# --- no provider: built-in fallback ------------------------------------------

def test_host_queries_empty_without_provider(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert DomainRouteProvider.wildcard_domains() == []
    assert DomainRouteProvider.proxy_servers() == []


def test_builtin_dns_records_without_provider(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(DomainRouteProvider, "_server_ip", staticmethod(lambda: ""))
    bench = _make_bench(tmp_path)
    _write_site(bench, "mysite")

    records = DomainRouteProvider(bench).generate_dns_records("mysite", "app.example.com")

    assert records == {"cname": [{"type": "CNAME", "host": "app.example.com", "value": "mysite"}], "a": []}


# --- end to end: provider proxy IPs reach the nginx config -------------------

def test_nginx_gates_tcp_peer_to_provider_proxy_servers(tmp_path: Path, monkeypatch) -> None:
    _install_provider(tmp_path, monkeypatch)
    config = NginxConfigRenderer(_make_bench(tmp_path)).generate_site_config(
        SiteConfig(name="site1.example.com", apps=["frappe"]), ssl_ready=False
    )

    assert "set_real_ip_from   203.0.113.10;" in config
    assert r'if ($realip_remote_addr ~ "^(203\.0\.113\.10|203\.0\.113\.11)$") { set $bench_from_proxy 1; }' in config
    assert "if ($bench_from_proxy = 0) { return 403; }" in config
    assert "deny               all;" not in config
    assert "X-Forwarded-For    $http_x_forwarded_for" in config
