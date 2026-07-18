"""Integration test for completing Frappe's setup wizard over HTTP."""

from __future__ import annotations

import json
import socket
import subprocess
import time
from pathlib import Path

import pytest
import requests

SITE = "site1.localhost"
ADMIN_PASSWORD = "admin"
GUNICORN_PORT = 8000
BASE_URL = f"http://127.0.0.1:{GUNICORN_PORT}"


def _wait_for_port(host: str, port: int, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(1.0)
    return False


# Fixtures
@pytest.fixture(scope="module")
def gunicorn_proc(bench_root: Path):
    """Start a single-worker gunicorn serving frappe, yield, then stop it."""
    gunicorn_bin = bench_root / "env" / "bin" / "gunicorn"
    proc = subprocess.Popen(
        [
            str(gunicorn_bin),
            "--bind", f"127.0.0.1:{GUNICORN_PORT}",
            "--workers", "1",
            "--timeout", "120",
            "frappe.app:application",
        ],
        cwd=bench_root / "sites",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if not _wait_for_port("127.0.0.1", GUNICORN_PORT, timeout=60):
        proc.terminate()
        try:
            _, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            stderr = b""
        pytest.fail(
            f"gunicorn did not become ready on port {GUNICORN_PORT} within 60 s.\n"
            f"stderr: {stderr.decode(errors='replace')}"
        )

    yield proc

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


# Tests
@pytest.mark.integration
class TestSetupWizard:

    def test_setup_wizard_completes(self, bench_root: Path, gunicorn_proc) -> None:
        """setup_complete succeeds or returns the idempotent ok response."""
        session = requests.Session()
        # Frappe routes the request to the correct site via the Host header.
        session.headers["Host"] = SITE

        # Authenticate as Administrator (created by bench new-site --admin-password).
        login = session.post(
            f"{BASE_URL}/api/method/login",
            data={"usr": "Administrator", "pwd": ADMIN_PASSWORD},
        )
        assert login.status_code == 200, (
            f"Login failed ({login.status_code}): {login.text}"
        )

        # Run the setup wizard.
        response = session.post(
            f"{BASE_URL}/api/method/"
            "frappe.desk.page.setup_wizard.setup_wizard.setup_complete",
            data={
                "args": json.dumps({
                    "language": "English",
                    "country": "United States",
                    "timezone": "America/New_York",
                    "currency": "USD",
                    "full_name": "Test Administrator",
                    "email": "admin@example.com",
                    "password": ADMIN_PASSWORD,
                })
            },
        )
        assert response.status_code == 200, (
            f"setup_complete returned {response.status_code}: {response.text}"
        )

        body = response.json()
        assert not body.get("exc"), (
            f"setup_complete raised a server-side exception:\n{body.get('exc')}"
        )

    def test_setup_wizard_idempotent(self, bench_root: Path, gunicorn_proc) -> None:
        """A second setup_complete call returns ok without rerunning."""
        session = requests.Session()
        session.headers["Host"] = SITE

        login = session.post(
            f"{BASE_URL}/api/method/login",
            data={"usr": "Administrator", "pwd": ADMIN_PASSWORD},
        )
        assert login.status_code == 200

        response = session.post(
            f"{BASE_URL}/api/method/"
            "frappe.desk.page.setup_wizard.setup_wizard.setup_complete",
            data={"args": json.dumps({})},
        )
        assert response.status_code == 200, (
            f"Second call to setup_complete failed ({response.status_code}): {response.text}"
        )

        body = response.json()
        assert not body.get("exc"), (
            f"Second call raised a server-side exception:\n{body.get('exc')}"
        )
        assert body.get("message", {}).get("status") == "ok", (
            f"Expected {{\"status\": \"ok\"}} on second call, got: {body.get('message')}"
        )
