import json
import os
from pathlib import Path

import pytest

from pilot.config import BenchConfig, SiteConfig
from pilot.core.adapters.domain_provider import DomainRouteProvider
from pilot.core.bench import Bench
from pilot.exceptions import BenchError, DomainConflictError, DomainProviderError
from pilot.managers.nginx import NginxConfigRenderer

_BENCH_DATA: dict = {
    "bench": {"name": "test-bench", "python": "3.14"},
    "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
    "mariadb": {"root_password": "root"},
    "redis": {"cache_port": 13000, "queue_port": 11000},
}

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
elif verb == "register":
    print(json.dumps({"public_scheme": "https", "origin_scheme": "https", "client_ip_source": "proxy_protocol_v2"}))
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


def test_generate_dns_records_returns_provider_output(tmp_path: Path, monkeypatch) -> None:
    _install_provider(tmp_path, monkeypatch)
    bench = _make_bench(tmp_path)
    _write_site(bench, "mysite")

    records = DomainRouteProvider(bench).generate_dns_records("mysite", "app.example.com")

    assert records == {
        "cname": [{"type": "CNAME", "host": "app.example.com", "value": "edge.example.com"}],
        "a": [],
    }


def test_generate_dns_records_passes_site_then_domain(tmp_path: Path, monkeypatch) -> None:
    log = _install_provider(tmp_path, monkeypatch)
    bench = _make_bench(tmp_path)
    _write_site(bench, "mysite")

    DomainRouteProvider(bench).generate_dns_records("mysite", "app.example.com")

    assert _calls(log) == ["generate-dns-records mysite app.example.com"]


def test_generate_dns_records_validates_locally_before_provider(tmp_path: Path, monkeypatch) -> None:
    """Local duplicate checks run before provider calls."""
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

    assert _calls(log) == ["register app.example.com", "proxy-servers"]
    saved = json.loads((bench.sites_path / "mysite" / "site_config.json").read_text())
    assert saved["domains"] == [{
        "domain": "app.example.com",
        "route": {
            "public_scheme": "https",
            "origin_scheme": "https",
            "client_ip_source": "proxy_protocol_v2",
        },
    }]


def test_register_rejects_missing_policy_and_rolls_back(tmp_path: Path, monkeypatch) -> None:
    provider = _DUMMY_PROVIDER.replace(
        'elif verb == "register":\n    print(json.dumps({"public_scheme": "https", "origin_scheme": "https", "client_ip_source": "proxy_protocol_v2"}))',
        'elif verb == "register":\n    pass',
    )
    log = _install_provider(tmp_path, monkeypatch, body=provider)
    bench = _make_bench(tmp_path)
    _write_site(bench, "mysite")

    with pytest.raises(DomainProviderError, match="route policy"):
        DomainRouteProvider(bench).register("mysite", "app.example.com")

    assert _calls(log) == ["register app.example.com", "deregister app.example.com"]


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


_TRANSPORT_FAILURE_PROVIDER = """#!/usr/bin/env python3
import sys
sys.stderr.write("provider config missing")
sys.exit(1)
"""


def test_register_declined_raises_domain_conflict(tmp_path: Path, monkeypatch) -> None:
    _install_provider(tmp_path, monkeypatch)
    monkeypatch.setenv("PROVIDER_FAIL", "register")
    bench = _make_bench(tmp_path)
    _write_site(bench, "mysite")

    with pytest.raises(DomainConflictError, match="register declined"):
        DomainRouteProvider(bench).register("mysite", "app.example.com")


def test_register_transport_failure_raises_domain_provider_error(tmp_path: Path, monkeypatch) -> None:
    _install_provider(tmp_path, monkeypatch, body=_TRANSPORT_FAILURE_PROVIDER)
    bench = _make_bench(tmp_path)
    _write_site(bench, "mysite")

    with pytest.raises(DomainProviderError, match="provider config missing"):
        DomainRouteProvider(bench).register("mysite", "app.example.com")


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

    assert records == {
        "cname": [{"type": "CNAME", "host": "app.example.com", "value": "mysite"}],
        "a": [],
    }


def test_a_domain_behind_cloudflare_is_accepted_with_a_warning(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(DomainRouteProvider, "_server_ip", staticmethod(lambda: "198.51.100.7"))
    addresses = {"app.example.com": {"104.16.1.2", "2606:4700::1"}}
    monkeypatch.setattr(DomainRouteProvider, "_resolve", staticmethod(lambda host: set(addresses.get(host, ()))))
    bench = _make_bench(tmp_path)
    _write_site(bench, "mysite")

    DomainRouteProvider(bench).register("mysite", "app.example.com")

    assert "app.example.com" in DomainRouteProvider(bench).domains("mysite")
    assert "Cloudflare" in capsys.readouterr().err


def test_a_domain_pointing_elsewhere_is_refused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(DomainRouteProvider, "_server_ip", staticmethod(lambda: "198.51.100.7"))
    addresses = {"app.example.com": {"104.16.1.2", "203.0.113.9"}}
    monkeypatch.setattr(DomainRouteProvider, "_resolve", staticmethod(lambda host: set(addresses.get(host, ()))))
    bench = _make_bench(tmp_path)
    _write_site(bench, "mysite")

    with pytest.raises(DomainConflictError, match="doesn't point here"):
        DomainRouteProvider(bench).register("mysite", "app.example.com")


def test_nginx_gates_tcp_peer_to_provider_proxy_servers(tmp_path: Path, monkeypatch) -> None:
    _install_provider(tmp_path, monkeypatch)
    config = NginxConfigRenderer(_make_bench(tmp_path)).generate_bench_config(
        [(SiteConfig(name="site1.example.com", apps=["frappe"]), [])], admin_ssl=False
    )

    assert "set_real_ip_from   203.0.113.10;" in config
    assert "203.0.113.10 1;" in config
    assert "203.0.113.11 1;" in config
    assert "if ($bench_from_proxy = 0) { return 403; }" in config
    assert "deny               all;" not in config
    assert "X-Forwarded-For    $http_x_forwarded_for" in config


def test_set_primary_waits_for_the_site_config_lock(tmp_path: Path, monkeypatch) -> None:
    import threading

    from pilot.internal.atomic_file import exclusive_file_lock

    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    bench = _make_bench(tmp_path)
    _write_site(bench, "site1.local", {"domains": ["shop.example.com"]})
    path = bench.sites_path / "site1.local" / "site_config.json"
    provider = DomainRouteProvider(bench)

    with exclusive_file_lock(path):
        writer = threading.Thread(target=provider.set_primary, args=("site1.local", "shop.example.com"))
        writer.start()
        writer.join(timeout=0.3)
        assert writer.is_alive()
        assert "host_name" not in json.loads(path.read_text())
    writer.join(timeout=5)

    assert json.loads(path.read_text())["host_name"] == "http://shop.example.com"


def test_a_provider_that_does_not_answer_fails_within_its_timeout(monkeypatch) -> None:
    import subprocess

    from pilot.core.adapters import domain_provider

    def hang(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(domain_provider, "which", lambda name: "/usr/local/bin/bench-domain-provider")
    monkeypatch.setattr(domain_provider.subprocess, "run", hang)

    with pytest.raises(DomainProviderError, match="did not answer within 30 seconds"):
        DomainRouteProvider.proxy_servers()
