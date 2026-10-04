from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from pilot.exceptions import RemoteSiteError
from pilot.integrations.frappe_site import RemoteFrappeSite


class FakeFrappe(BaseHTTPRequestHandler):
    """Answers the few endpoints a remote restore uses, like a Frappe site does."""

    backups: ClassVar[dict] = {
        "database": "./s/private/backups/20261004_020000-s-database.sql.gz",
        "public": "./s/private/backups/20261004_020000-s-files.tar",
        "private": None,
        "config": None,
    }

    def do_POST(self) -> None:
        form = urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode())
        is_right = form["pwd"] == ["right"]
        self._send(
            {"message": "Logged In" if is_right else "Incorrect password"},
            status=200 if is_right else 401,
            cookie="sid=abc" if is_right else "",
        )

    def do_GET(self) -> None:
        path = self.path.partition("?")[0]
        if "sid=abc" not in (self.headers.get("Cookie") or ""):
            self._send({}, status=403)
            return
        if path.startswith("/backups/"):
            self._send_bytes(f"content of {path.rsplit('/', 1)[1]}".encode())
            return
        method = path.removeprefix("/api/method/")
        if method == "frappe.utils.backups.fetch_latest_backups":
            self._send({"message": FakeFrappe.backups})
            return
        self._send({}, status=404)

    def _send(self, body: dict, status: int = 200, cookie: str = "") -> None:
        self.send_response(status)
        if cookie:
            self.send_header("Set-Cookie", f"{cookie}; Path=/")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def _send_bytes(self, data: bytes) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def remote_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeFrappe)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_a_wrong_password_is_reported_before_any_work(remote_url: str) -> None:
    with pytest.raises(RemoteSiteError, match="password is invalid"):
        RemoteFrappeSite(remote_url, "wrong").login()


def test_the_latest_backup_is_read_and_downloaded(remote_url: str, tmp_path: Path) -> None:
    remote = RemoteFrappeSite(remote_url, "right")
    remote.login()

    timestamp, files = remote.get_latest_run()
    downloaded = remote.download_backup(files["public"], tmp_path)

    assert timestamp == "20261004_020000"
    assert set(files) == {"database", "public"}
    assert downloaded.read_text() == "content of 20261004_020000-s-files.tar"


def test_an_unreachable_site_is_a_clear_error() -> None:
    with pytest.raises(RemoteSiteError, match="Could not reach"):
        RemoteFrappeSite("http://127.0.0.1:9", "right").login()


def test_the_latest_run_leaves_out_files_of_older_runs() -> None:
    from unittest.mock import patch

    latest = {
        "database": "./s/20261004_020000-s-database.sql.gz",
        "public": "./s/20261001_020000-s-files.tar",
        "private": None,
        "config": "./s/20261004_020000-s-site_config_backup.json",
    }
    with patch.object(RemoteFrappeSite, "get_latest_backups", return_value=latest):
        timestamp, files = RemoteFrappeSite("old.example.com", "pw").get_latest_run()

    assert timestamp == "20261004_020000"
    assert set(files) == {"database", "config"}
