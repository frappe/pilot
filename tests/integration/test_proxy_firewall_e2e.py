"""Trusted-proxy and firewall integration test over live HTTP."""

from __future__ import annotations

import contextlib
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.managers.nginx import NginxManager

pytestmark = pytest.mark.integration

_PROXY_SRC = "127.0.0.55"
_BACKEND_PORT = 8961
_BENCH_PORT = 8962
_EDGE_PORT = 8963

_BENCH_DATA: dict = {
    "bench": {"name": "test-bench", "python": "3.14", "http_port": _BACKEND_PORT},
    "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
    "mariadb": {"root_password": "root"},
    "redis": {"cache_port": 13000, "queue_port": 11000},
}


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


class _EchoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self):
        body = self.headers.get("X-Real-IP", "").encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence
        pass


@pytest.fixture
def backend():
    server = ThreadingHTTPServer(("127.0.0.1", _BACKEND_PORT), _EchoHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    yield
    server.shutdown()


def _wait_for_port(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with contextlib.suppress(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        time.sleep(0.05)
    raise RuntimeError(f"nothing listening on {port}")


def _run_nginx(prefix: Path, body: str) -> subprocess.Popen:
    prefix.mkdir(parents=True, exist_ok=True)
    conf = prefix / "nginx.conf"
    conf.write_text(body)
    return subprocess.Popen(
        ["nginx", "-p", str(prefix), "-c", str(conf), "-g", "daemon off;"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)


def _http_wrapper(prefix: Path, http_body: str) -> str:
    log_format = (
        "    log_format pilot_access '$remote_addr [$time_local] \"$request_method $uri\" "
        "$status \"$host\" $request_time';\n"
    )
    return (
        f"pid {prefix}/nginx.pid;\n"
        f"error_log {prefix}/error.log;\n"
        "events {}\n"
        f"http {{\n{log_format}    access_log off;\n{http_body}}}\n"
    )


@pytest.fixture
def edge(tmp_path: Path):
    """The always-on edge proxy: forwards to the bench from source 127.0.0.55 and
    turns X-Test-Client into the X-Forwarded-For the bench trusts."""
    body = _http_wrapper(
        tmp_path / "edge",
        "    server {\n"
        f"        listen 127.0.0.1:{_EDGE_PORT};\n"
        "        location / {\n"
        f"            proxy_bind {_PROXY_SRC};\n"
        f"            proxy_pass http://127.0.0.1:{_BENCH_PORT};\n"
        "            proxy_set_header Host $host;\n"
        "            proxy_set_header X-Forwarded-For $http_x_test_client;\n"
        "        }\n"
        "    }\n",
    )
    proc = _run_nginx(tmp_path / "edge", body)
    _wait_for_port(_EDGE_PORT)
    yield
    _stop(proc)


@contextlib.contextmanager
def _bench_nginx(tmp_path: Path, firewall: dict | None):
    data = {**_BENCH_DATA}
    if firewall is not None:
        data = {**data, "firewall": firewall}
    bench = Bench(BenchConfig._from_dict(data), tmp_path / "bench")
    bench.config.nginx.http_port = _BENCH_PORT
    bench.create_directories()
    site = bench.sites_path / "site1.localhost"
    site.mkdir(parents=True, exist_ok=True)
    (site / "site_config.json").write_text("{}")

    manager = NginxManager(bench)
    manager._renderer._proxy_servers_cache = [_PROXY_SRC]
    manager.generate_config(ssl_ready=False)

    prefix = tmp_path / "bench-run"
    body = _http_wrapper(prefix, f"    include {bench.config_path}/nginx/include.conf;\n")
    proc = _run_nginx(prefix, body)
    try:
        _wait_for_port(_BENCH_PORT)
        yield
    finally:
        _stop(proc)


def _get(port: int, client_ip: str | None, path: str = "/") -> tuple[int, str]:
    headers = {"Host": "site1.localhost"}
    if client_ip is not None:
        headers["X-Test-Client"] = client_ip
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def test_direct_connection_is_blocked_only_proxy_gets_through(tmp_path, backend, edge):
    with _bench_nginx(tmp_path, firewall=None):
        # Straight to the bench: TCP peer 127.0.0.1 is not a trusted proxy -> 403.
        assert _get(_BENCH_PORT, "1.2.3.4")[0] == 403
        # Through the edge: peer is the proxy; the real client IP reaches the backend.
        status, body = _get(_EDGE_PORT, "1.2.3.4")
        assert status == 200
        assert body == "1.2.3.4"
        # ACME stays reachable on a direct hit (404 = no such token, not 403 = gated),
        # so cert issuance/renewal survives certbot reaching the bench directly.
        assert _get(_BENCH_PORT, None, "/.well-known/acme-challenge/probe")[0] == 404


def test_allowlist_filters_client_but_never_the_proxy(tmp_path, backend, edge):
    firewall = {"enabled": True, "default": "deny", "rules": [{"ip": "1.2.3.4", "action": "allow"}]}
    with _bench_nginx(tmp_path, firewall):
        assert _get(_EDGE_PORT, "1.2.3.4")[0] == 200  # allowed client
        assert _get(_EDGE_PORT, "9.9.9.9")[0] == 403  # client not on the allowlist
        # No X-Forwarded-For: $remote_addr stays the proxy IP; the allowlist must
        # not block it, or the whole bench goes dark.
        assert _get(_EDGE_PORT, None)[0] == 200


def test_blocklist_cannot_block_the_proxy_even_when_denied(tmp_path, backend, edge):
    firewall = {
        "enabled": True,
        "default": "allow",
        "rules": [{"ip": "9.9.9.9", "action": "deny"}, {"ip": _PROXY_SRC, "action": "deny"}],
    }
    with _bench_nginx(tmp_path, firewall):
        assert _get(_EDGE_PORT, "1.2.3.4")[0] == 200  # client allowed by default
        assert _get(_EDGE_PORT, "9.9.9.9")[0] == 403  # client explicitly denied
        # Proxy IP is explicitly denied, yet an XFF-less request still passes: the
        # proxy allow is emitted first and access rules are first-match.
        assert _get(_EDGE_PORT, None)[0] == 200
