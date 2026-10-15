from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from pilot.config import BenchConfig, SiteConfig
from pilot.config.central import HostnameAlias
from pilot.config.route import RoutePolicy
from pilot.core.bench import Bench
from pilot.managers.nginx import NginxConfigRenderer

pytestmark = pytest.mark.integration


class HeaderEcho(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(dict(self.headers)).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.do_GET()

    def log_message(self, *args):
        pass


@pytest.fixture
def upstream():
    server = ThreadingHTTPServer(("127.0.0.1", 0), HeaderEcho)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("route", ["direct", "edge_https", "alias"])
def test_generated_socketio_origin_headers(tmp_path: Path, upstream: int, route: str):
    if not shutil.which("nginx"):
        pytest.skip("nginx not on PATH")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    config = BenchConfig._from_dict({"bench": {"name": "socket-test", "socketio_port": upstream}})
    config.nginx.http_port = port
    bench = Bench(config, tmp_path)
    bench.create_directories()
    site = SiteConfig(name="atlas.localhost", apps=["frappe"])
    hostname = "atlas.localhost"
    if route == "edge_https":
        site.route = RoutePolicy("https", "http", "direct")
    if route == "alias":
        hostname = "site-123.example.test"
        config.central.enabled = True
        config.central.hostname_aliases = [
            HostnameAlias(type="site", pattern="site-*.example.test", target=site.name, redirect=False)
        ]
    renderer = NginxConfigRenderer(bench)
    renderer._proxy_servers_cache = []
    generated = renderer.generate_bench_config([(site, [])], admin_ssl=False)
    conf = tmp_path / "nginx.conf"
    conf.write_text(
        f"pid {tmp_path}/nginx.pid;\nerror_log {tmp_path}/error.log;\nevents {{}}\n"
        "http {\nlog_format pilot_access '$request';\naccess_log off;\n" + generated + "\n}\n"
    )
    command = ["nginx", "-p", str(tmp_path), "-c", str(conf)]
    subprocess.run([*command, "-t"], check=True, capture_output=True)
    process = subprocess.Popen([*command, "-g", "daemon off;"], stderr=subprocess.PIPE)
    try:
        wait_for_nginx(process, port)
        host = f"{hostname}:{port}"
        scheme = "https" if route == "edge_https" else "http"
        for origin in (None, f"{scheme}://{host}", "https://foreign.example", "null"):
            for method in ("GET", "POST"):
                headers = {"Host": host}
                if origin is not None:
                    headers["Origin"] = origin
                request = urllib.request.Request(
                    f"http://127.0.0.1:{port}/socket.io/?EIO=4&transport=polling",
                    headers=headers,
                    data=b"40/atlas.localhost," if method == "POST" else None,
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    received = json.load(response)
                assert received["Origin"] == (origin if origin is not None else f"{scheme}://{host}")
                assert received["Host"] == hostname
                assert received["X-Frappe-Site-Name"] == site.name
    finally:
        process.terminate()
        process.communicate(timeout=10)


def wait_for_nginx(process: subprocess.Popen, port: int):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        assert process.poll() is None, process.stderr.read().decode()
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.05)
    raise AssertionError("nginx did not start")
