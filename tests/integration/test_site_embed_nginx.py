from __future__ import annotations

import http.client
import shutil
import socket
import subprocess
import time

import pytest

from pilot.config import SiteConfig
from tests.pilot.managers.test_nginx_config import _cloud_config

pytestmark = pytest.mark.integration


@pytest.fixture
def embed_server(tmp_path, monkeypatch):
    nginx = shutil.which("nginx")
    if not nginx:
        pytest.skip("nginx not on PATH")
    static = tmp_path / "static"
    embed = static / "in-app-embed/cloud-settings"
    (embed / "esm/chunks").mkdir(parents=True)
    for name in ("cloud-settings.js", "esm/cloud-settings.js", "esm/chunks/panel-abc123.js"):
        (embed / name).write_text(f"// {name}")
    monkeypatch.setattr("pilot.managers.nginx._admin_static_dir", lambda: static)
    site = SiteConfig(name="site.local", apps=["frappe"], domains=["custom.local"])
    config = _cloud_config(
        tmp_path,
        [(site, False)],
        hostname_mappings={"site-*.example.com": site.name},
        redirect=False,
    )
    socket_path = tmp_path / "nginx.sock"
    config = config.replace("listen 80;", f"listen unix:{socket_path};").replace("listen [::]:80;", "")
    conf = tmp_path / "nginx.conf"
    (tmp_path / "logs").mkdir()
    conf.write_text(
        f"pid {tmp_path}/nginx.pid;\nerror_log {tmp_path}/error.log;\nevents {{}}\nhttp {{\n"
        "types { application/javascript js; }\nmap $remote_addr $pilot_client_addr { default $remote_addr; }\n"
        'log_format pilot_access "$request";\n'
        f"client_body_temp_path {tmp_path}/client_body;\nproxy_temp_path {tmp_path}/proxy;\n"
        f"fastcgi_temp_path {tmp_path}/fastcgi;\nuwsgi_temp_path {tmp_path}/uwsgi;\nscgi_temp_path {tmp_path}/scgi;\n"
        f"access_log off;\n{config}\n}}\n"
    )
    process = subprocess.Popen(
        [
            nginx,
            "-e",
            str(tmp_path / "error.log"),
            "-p",
            str(tmp_path),
            "-c",
            str(conf),
            "-g",
            "daemon off; master_process off;",
        ]
    )
    try:
        for _ in range(100):
            if socket_path.exists() or process.poll() is not None:
                break
            time.sleep(0.02)
        assert process.poll() is None, (tmp_path / "error.log").read_text()
        yield socket_path
    finally:
        process.terminate()
        process.wait(timeout=5)


@pytest.mark.parametrize("host", ["site.local", "custom.local", "site-123.example.com"])
def test_site_and_alias_serve_embeds_without_an_admin_upstream(embed_server, host):
    for name in ("cloud-settings.js", "esm/cloud-settings.js", "esm/chunks/panel-abc123.js"):
        status, headers, body = _get(embed_server, host, f"/_pilot/embed/cloud-settings/{name}")
        assert status == 200
        assert headers["Content-Type"] == "application/javascript"
        assert body == f"// {name}".encode()
        cache_control = ", ".join(headers.get_all("Cache-Control", []))
        assert ("immutable" in cache_control) == name.startswith("esm/chunks/")
        if not name.startswith("esm/chunks/"):
            assert headers["Cache-Control"] == "no-cache"
    status, _, body = _get(embed_server, host, "/_pilot/embed/cloud-settings/missing.js")
    assert status == 404
    assert b"// cloud-settings" not in body


def _get(socket_path, host, path):
    connection = http.client.HTTPConnection(host, timeout=2)
    connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.sock.settimeout(2)
    connection.sock.connect(str(socket_path))
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, response.headers, response.read()
    finally:
        connection.close()
