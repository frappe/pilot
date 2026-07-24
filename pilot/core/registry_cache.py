"""Tamper-checked local clone of the marketplace registry."""

from __future__ import annotations

import shlex
import subprocess
import sys
import time
from pathlib import Path

from pilot.exceptions import CommandError, RegistryUnavailableError
from pilot.managers.cron import CronManager
from pilot.utils import run_command

REGISTRY_URL = "https://github.com/frappe/marketplace"

_REFRESH_INTERVAL_SECONDS = 60 * 60
_LS_REMOTE_TIMEOUT_SECONDS = 15
_CRON_JOB_KEY = "marketplace-registry-refresh"
_CRON_SCHEDULE = "0 3 * * *"  # once a day, 03:00


class RegistryCache:
    """Shallow, read-only clone at <cli_root>/registry-cache."""

    def __init__(self, cli_root: Path) -> None:
        self._cli_root = cli_root

    @property
    def path(self) -> Path:
        return self._cli_root / "registry-cache"

    @property
    def apps_json_path(self) -> Path:
        return self.path / "apps.json"

    @property
    def _last_checked_path(self) -> Path:
        return self._cli_root / "registry-cache.last_checked"

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
        log_file = self._cli_root / "logs" / "registry-refresh.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        python, cli_root, log = (shlex.quote(str(p)) for p in (sys.executable, self._cli_root, log_file))
        command = f"{python} -m pilot.core.registry_cache {cli_root} >> {log} 2>&1"
        CronManager(self._cli_root).set_schedule(_CRON_JOB_KEY, _CRON_SCHEDULE, command)


if __name__ == "__main__":
    # Invoked by the cron entry installed via install_daily_refresh_cron.
    RegistryCache(Path(sys.argv[1])).ensure_fresh()
