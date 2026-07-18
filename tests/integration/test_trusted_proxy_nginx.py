"""Integration test for nginx trusted-proxy config."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.managers.nginx import NginxManager

pytestmark = pytest.mark.integration

_BENCH_DATA: dict = {
    "bench": {"name": "test-bench", "python": "3.14"},
    "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
    "mariadb": {"root_password": "root"},
    "redis": {"cache_port": 13000, "queue_port": 11000},
}

# Minimal provider that only answers proxy-servers - enough to drive nginx config.
_PROVIDER = """#!/usr/bin/env python3
import json, sys
if sys.argv[1:2] == ["proxy-servers"]:
    print(json.dumps(["203.0.113.10", "203.0.113.11"]))
"""

_PROXIES = ["203.0.113.10", "203.0.113.11"]

# `nginx -t` opens the listen sockets, so use an unprivileged port (it runs as
# the test user, which can't bind 80).
_HTTP_PORT = 8973


def _missing_tooling() -> str | None:
    if shutil.which("nginx") is None:
        return "nginx not on PATH"
    info = subprocess.run(["nginx", "-V"], capture_output=True, text=True).stderr
    if "http_realip_module" not in info:
        return "nginx built without http_realip_module"
    return None


@pytest.fixture(autouse=True)
def _require_nginx():
    reason = _missing_tooling()
    if reason:
        pytest.skip(reason)


def _install_provider(tmp_path: Path, monkeypatch) -> None:
    exe = tmp_path / "bin" / "bench-domain-provider"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(_PROVIDER)
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(exe.parent) + os.pathsep + os.environ["PATH"])


def _make_bench(tmp_path: Path) -> Bench:
    bench = Bench(BenchConfig._from_dict(_BENCH_DATA), tmp_path)
    bench.config.nginx.http_port = _HTTP_PORT
    site = bench.sites_path / "site1.localhost"
    site.mkdir(parents=True, exist_ok=True)
    (site / "site_config.json").write_text("{}")
    return bench


def _wrapper_conf(tmp_path: Path, include_conf: Path) -> Path:
    """A self-contained nginx.conf that pulls in the bench's generated vhosts."""
    conf = tmp_path / "nginx.conf"
    conf.write_text(
        f"pid {tmp_path}/nginx.pid;\n"
        f"error_log {tmp_path}/error.log;\n"
        "events {}\n"
        "http {\n"
        f"    access_log {tmp_path}/access.log;\n"
        f"    include {include_conf};\n"
        "}\n"
    )
    return conf


def test_generated_trusted_proxy_config_passes_nginx_t(tmp_path: Path, monkeypatch) -> None:
    _install_provider(tmp_path, monkeypatch)
    bench = _make_bench(tmp_path)

    NginxManager(bench).generate_config(ssl_ready=False)

    nginx_dir = bench.config_path / "nginx"
    site_conf = (nginx_dir / "include.conf").read_text()
    for ip in _PROXIES:
        assert f"set_real_ip_from   {ip};" in site_conf
    assert "real_ip_header     X-Forwarded-For;" in site_conf
    # Gate TCP connections to the proxies on the real peer, not the rewritten client.
    assert (
        r'if ($realip_remote_addr ~ "^(203\.0\.113\.10|203\.0\.113\.11)$") { set $bench_from_proxy 1; }'
        in site_conf
    )
    assert "if ($bench_from_proxy = 0) { return 403; }" in site_conf
    # The ACME challenge must stay reachable directly, else cert issuance fails.
    assert r'if ($request_uri ~ "^/\.well-known/acme-challenge/") { set $bench_from_proxy 1; }' in site_conf
    assert "deny               all;" not in site_conf
    assert "X-Forwarded-For    $http_x_forwarded_for" in site_conf

    conf = _wrapper_conf(tmp_path, nginx_dir / "include.conf")
    result = subprocess.run(
        ["nginx", "-t", "-p", str(tmp_path), "-c", str(conf)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
