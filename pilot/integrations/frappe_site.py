from __future__ import annotations

import http.client
import json
import shutil
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from typing import IO

from pilot.exceptions import RemoteSiteError

_REQUEST_TIMEOUT_SECONDS = 30
# Per socket read, so a large download may take as long as it needs but a stalled one ends.
_DOWNLOAD_READ_TIMEOUT_SECONDS = 300


class RemoteFrappeSite:
    """A Frappe site reached as its Administrator, to copy its latest backup. Method calls
    use GET: a session-cookie POST needs a CSRF token that only Frappe's desk receives."""

    def __init__(self, site: str, password: str) -> None:
        site = site.strip().rstrip("/")
        self.url = site if site.startswith(("https://", "http://")) else f"https://{site}"
        self.password = password
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def login(self) -> None:
        """Fail with a clear message for a wrong password, 2FA, or an unreachable site."""
        body = urllib.parse.urlencode({"usr": "Administrator", "pwd": self.password}).encode()
        try:
            response = self._json(self._open("/api/method/login", data=body))
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise RemoteSiteError("The administrator password is invalid.") from error
            raise RemoteSiteError(f"The site refused the login (HTTP {error.code}).") from error
        if "verification" in response:
            raise RemoteSiteError("Sites with two-factor authentication are not supported.")

    def get_latest_run(self) -> tuple[str, dict[str, str]]:
        """The newest backup run on the remote: its timestamp and its files by part. Frappe
        picks the newest file of each kind on its own, so files of older runs are left out."""
        from pilot.core.site.backups import parse_backup_timestamp

        backups = {part: path for part, path in self.get_latest_backups().items() if path}
        timestamps = {part: parse_backup_timestamp(Path(path).name) or "" for part, path in backups.items()}
        timestamp = max(timestamps.values(), default="")
        return timestamp, {part: path for part, path in backups.items() if timestamp and timestamps[part] == timestamp}

    def get_latest_backups(self) -> dict[str, str]:
        """Paths of the remote's newest backup files, by part (database, public, private, config)."""
        return self._call("frappe.utils.backups.fetch_latest_backups")

    def open_backup(self, path: str) -> IO[bytes]:
        """A backup file as a stream. Frappe serves /backups/<name> to System Managers."""
        return self._open(f"/backups/{urllib.parse.quote(Path(path).name)}", timeout=_DOWNLOAD_READ_TIMEOUT_SECONDS)

    def download_backup(self, path: str, directory: Path) -> Path:
        target = directory / Path(path).name
        with self.open_backup(path) as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination)
        return target

    def _call(self, method: str, **params: str) -> dict:
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        try:
            return self._json(self._open(f"/api/method/{method}{query}")).get("message") or {}
        except urllib.error.HTTPError as error:
            raise RemoteSiteError(f"{self.url} rejected {method}: HTTP {error.code}.") from error

    def _open(self, path: str, data: bytes | None = None, timeout: float = _REQUEST_TIMEOUT_SECONDS):
        try:
            return self._opener.open(urllib.request.Request(f"{self.url}{path}", data=data), timeout=timeout)
        except urllib.error.HTTPError:
            raise
        except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException) as error:
            raise RemoteSiteError(f"Could not reach {self.url}: {error}") from error

    def _json(self, response) -> dict:
        with response:
            try:
                return json.loads(response.read() or b"{}")
            except ValueError as error:
                raise RemoteSiteError(f"{self.url} did not answer like a Frappe site.") from error
