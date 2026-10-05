from __future__ import annotations

import contextlib
import json
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import FrappeCloudError
from pilot.integrations.frappe_cloud import FrappeCloud
from pilot.internal.atomic_file import atomic_write_private_text, exclusive_file_lock
from pilot.utils import make_private_directory

if TYPE_CHECKING:
    from pilot.core.site import Site

# No 0, O, 1, I or L, so the code reads the same when typed by hand.
PASS_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
PASS_CODE_LENGTH = 8


class SiteFrappeCloud:
    """This site's link to a Frappe Cloud v1 site whose backups it may restore. The file
    holds the access token, so it is private and outside the site directory that backups copy."""

    def __init__(self, site: Site) -> None:
        self.site = site

    @property
    def path(self) -> Path:
        return self.site.bench.config_path / "frappe_cloud" / f"{self.site.config.name}.json"

    @property
    def connection(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    @property
    def client(self) -> FrappeCloud:
        connection = self.connection
        if not connection:
            raise FrappeCloudError("Connect to Frappe Cloud first.")
        return FrappeCloud(connection["url"], connection["token"])

    def connect(self, domain: str) -> dict:
        """Ask Frappe Cloud for access. Its team approves with the pass code shown here."""
        url = self.site.bench.config.frappe_cloud.url
        code = make_pass_code()
        response = FrappeCloud(url).request_access(domain, code)
        connection = {
            "url": url,
            "token": response["token"],
            "code": code,
            "remote_site": response["site"],
            "approval_url": response["approval_url"],
        }
        make_private_directory(self.path.parent, parents=True)
        atomic_write_private_text(self.path, json.dumps(connection))
        return connection | {"status": "Pending"}

    def get_status(self) -> dict:
        connection = self.connection
        status = self.client.get_status()
        return {
            "status": status["status"],
            "remote_site": connection["remote_site"],
            "approval_url": connection["approval_url"],
            "code": connection["code"],
        }

    def disconnect(self, token: str = "") -> None:
        """Revoke `token`, or the current one. A restore passes the token it was queued with, so
        a connection made since then stays. Frappe Cloud ends the access after 12 hours anyway,
        so a failed revoke is ignored."""
        make_private_directory(self.path.parent, parents=True)
        with exclusive_file_lock(self.path):
            connection = self.connection
            token = token or connection.get("token", "")
            if connection.get("token") == token:
                self.path.unlink(missing_ok=True)
        if not token:
            return
        url = connection.get("url") or self.site.bench.config.frappe_cloud.url
        with contextlib.suppress(FrappeCloudError):
            FrappeCloud(url, token).revoke()


def make_pass_code() -> str:
    """A code of letters and digits. Frappe Cloud refuses one with only digits."""
    while True:
        code = "".join(secrets.choice(PASS_CODE_ALPHABET) for _ in range(PASS_CODE_LENGTH))
        if not code.isdigit():
            return code
