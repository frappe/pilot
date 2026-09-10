"""Tamper-checked local clone of the marketplace registry."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from pilot.exceptions import CommandError, RegistryUnavailableError
from pilot.internal.git import GitRepo
from pilot.managers.cron import CronManager, cron_module_command
from pilot.utils import run_command

REGISTRY_URL = "https://github.com/frappe/marketplace"

_REFRESH_INTERVAL_SECONDS = 60 * 60
_LS_REMOTE_TIMEOUT_SECONDS = 15
_CRON_JOB_KEY = "marketplace-registry-refresh"
_CRON_SCHEDULE = "0 3 * * *"  # once a day, 03:00
_APP_NAME = re.compile(r"[a-z][a-z0-9_]*")


class RegistryCache:
    """Shallow, read-only clone at <cli_root>/registry-cache."""

    def __init__(self, cli_root: Path) -> None:
        self._cli_root = cli_root

    @property
    def path(self) -> Path:
        return self._cli_root / "system" / "registry-cache"

    @property
    def index_path(self) -> Path:
        return self.path / "apps.json"

    @property
    def _last_checked_path(self) -> Path:
        return self._cli_root / "system" / "registry-cache.last_checked"

    def load(self) -> list[dict]:
        """The app index, without releases - those are read per app by `releases`,
        so the index stays cheap however many releases the registry grows."""
        self.ensure_fresh()
        entries = self._read_json(self.index_path)
        if not isinstance(entries, list):
            raise RegistryUnavailableError(f"The marketplace index is not a list of apps: {self.index_path}")
        for entry in entries:
            self._reject_foreign_pointer(entry)
            entry.pop("releases")
        return entries

    def releases(self, app_name: str) -> list[dict]:
        """Releases for one indexed app. Assumes `load` has already run, so it does
        not re-check freshness - that check shells out to git."""
        if not _APP_NAME.fullmatch(app_name):
            raise RegistryUnavailableError(f"{app_name!r} is not a marketplace app name.")
        payload = self._read_json(self.path / "apps" / f"{app_name}.json")
        releases = payload.get("releases") if isinstance(payload, dict) else None
        if not isinstance(releases, list):
            raise RegistryUnavailableError(f"apps/{app_name}.json does not hold a 'releases' list.")
        return releases

    @staticmethod
    def _reject_foreign_pointer(entry: dict) -> None:
        """An entry may only point 'releases' at its own apps/<name>.json."""
        name = entry.get("name") or ""
        pointer = entry.get("releases")
        expected = f"apps/{name}.json"
        if not _APP_NAME.fullmatch(name) or pointer != expected:
            raise RegistryUnavailableError(
                f"The marketplace entry {name or entry!r} must point 'releases' at "
                f"{expected!r}, not {pointer!r} - the registry cache is unusable."
            )

    def _read_json(self, path: Path):
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryUnavailableError(
                f"Could not read the marketplace registry at {path}: {exc}"
            ) from exc

    def ensure_fresh(self) -> None:
        """Clone on first use; later reject tampering and refresh hourly."""
        if not self._is_cloned():
            self._clone()
            self._touch_last_checked()
            return

        self._reject_if_tampered()
        if self._refresh_due():
            self._refresh()
            self._touch_last_checked()

    def _is_cloned(self) -> bool:
        return (self.path / ".git").is_dir()

    def _clone(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            run_command(["git", "clone", "--depth", "1", REGISTRY_URL, str(self.path)])
        except CommandError as exc:
            raise RegistryUnavailableError(f"Could not clone marketplace registry:\n{exc.message}") from exc
        self.install_daily_refresh_cron()

    def _reject_if_tampered(self) -> None:
        try:
            result = run_command(["git", "-C", str(self.path), "status", "--porcelain"])
        except CommandError as exc:
            raise RegistryUnavailableError(
                "The marketplace registry cache is corrupted (git status failed) - "
                f"restore it before using get-app/marketplace: {self.path}"
            ) from exc
        if result.stdout.decode().strip():
            raise RegistryUnavailableError(
                "The marketplace registry cache has been modified manually - "
                f"restore it before using get-app/marketplace: {self.path}"
            )

    def _refresh_due(self) -> bool:
        if not self._last_checked_path.exists():
            return True
        last_checked = self._last_checked_path.stat().st_mtime
        return time.time() - last_checked >= _REFRESH_INTERVAL_SECONDS

    def _refresh(self) -> None:
        remote_head = self._remote_head_sha()
        if remote_head is None:
            return  # offline - keep serving the existing clone
        try:
            local_head = (
                run_command(["git", "-C", str(self.path), "rev-parse", "HEAD"]).stdout.decode().strip()
            )
            if remote_head == local_head:
                return
            GitRepo(self.path).prune_stale_temp_packs()
            run_command(["git", "-C", str(self.path), "fetch", "--depth", "1", "origin", "HEAD"])
            run_command(["git", "-C", str(self.path), "reset", "--hard", "FETCH_HEAD"])
        except CommandError:
            return  # local git trouble or network dropped mid-fetch - keep serving the existing clone

    def _remote_head_sha(self) -> str | None:
        try:
            result = run_command(
                ["git", "ls-remote", REGISTRY_URL, "HEAD"], timeout=_LS_REMOTE_TIMEOUT_SECONDS
            )
        except (CommandError, subprocess.SubprocessError, OSError):
            return None
        stdout = result.stdout.decode()
        line = stdout.splitlines()[0] if stdout else ""
        sha = line.split("\t", 1)[0].strip()
        return sha or None

    def _touch_last_checked(self) -> None:
        self._last_checked_path.touch()

    def install_daily_refresh_cron(self) -> None:
        """Register the daily cache refresh cron entry."""
        log_file = self._cli_root / "system" / "registry-cache.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        command = cron_module_command("pilot.core.registry_cache", [self._cli_root], log_file)
        CronManager(self._cli_root).set_schedule(_CRON_JOB_KEY, _CRON_SCHEDULE, command)


if __name__ == "__main__":
    # Invoked by the cron entry installed via install_daily_refresh_cron.
    RegistryCache(Path(sys.argv[1])).ensure_fresh()
