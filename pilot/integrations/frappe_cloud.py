from __future__ import annotations

import http.client
import json
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import IO

from pilot.exceptions import FrappeCloudError

_REQUEST_TIMEOUT_SECONDS = 30
_DOWNLOAD_READ_TIMEOUT_SECONDS = 300
_API = "/api/method/press.api.v1_migration"
_TOKEN_HEADER = "X-Press-Migration-Token"
# Cloudflare's Browser Integrity Check blocks urllib's default User-Agent with HTTP 403.
_HEADERS = {"User-Agent": "pilot"}


class FrappeCloud:
    """Frappe Cloud's v1 migration API, called with the token of one approved request."""

    def __init__(self, url: str, token: str = "") -> None:
        self.url = url.rstrip("/")
        self.token = token

    def request_access(self, domain: str, code: str) -> dict:
        return self._call("request_access", data={"domain": domain, "code": code})

    def get_status(self) -> dict:
        return self._call("get_status")

    def get_backups(self, start: int = 0) -> list[dict]:
        return self._call("get_backups", params={"start": start})

    def get_running_backup(self) -> str | None:
        return self._call("get_running_backup")

    def take_backup(self) -> str:
        return self._call("take_backup", data={})

    def get_backup_status(self, backup: str) -> dict:
        return self._call("get_backup_status", params={"backup": backup})

    def get_download_links(self, backup: str) -> dict[str, str]:
        return self._call("get_download_links", data={"backup": backup})

    def revoke(self) -> None:
        self._call("revoke", data={})

    def _call(self, method: str, params: dict | None = None, data: dict | None = None):
        """GET without `data`, POST with it. Frappe's error message is raised as is."""
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        request = urllib.request.Request(f"{self.url}{_API}.{method}{query}", data=body, headers=_HEADERS)
        if self.token:
            request.add_header(_TOKEN_HEADER, self.token)
        try:
            with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
                return json.loads(response.read() or b"{}").get("message")
        except urllib.error.HTTPError as error:
            raise FrappeCloudError(get_error_message(error)) from error
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as error:
            raise FrappeCloudError(f"Could not reach {self.url}: {error}") from error


def open_download_link(url: str) -> IO[bytes]:
    """A backup file from its pre-signed link. The link carries its own authorization.
    urllib also opens file:// and ftp:// links, so a link from Frappe Cloud must be HTTP."""
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise FrappeCloudError("Frappe Cloud sent a download link that is not HTTP.")
    try:
        request = urllib.request.Request(url, headers=_HEADERS)
        return urllib.request.urlopen(request, timeout=_DOWNLOAD_READ_TIMEOUT_SECONDS)
    except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
        raise FrappeCloudError(f"Could not download the backup: {error}") from error


def download_backup(url: str, directory: Path) -> Path:
    """Saved under the name in the link, which tells the restore what part the file holds."""
    target = directory / Path(urllib.parse.urlsplit(url).path).name
    with open_download_link(url) as source, target.open("wb") as destination:
        shutil.copyfileobj(source, destination)
    return target


def get_error_message(error: urllib.error.HTTPError) -> str:
    """The first message Frappe sent with the error, or the HTTP status."""
    try:
        messages = json.loads(json.loads(error.read() or b"{}").get("_server_messages") or "[]")
        return json.loads(messages[0])["message"]
    except (ValueError, IndexError, KeyError, TypeError):
        return f"Frappe Cloud answered with HTTP {error.code}."
