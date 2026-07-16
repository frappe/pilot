"""Local git-clone cache of the external marketplace registry repo.

The registry (apps_v2.json) lives in a separate, community-editable GitHub
repo rather than in this codebase. `RegistryCache` keeps one shared clone of
it per pilot install (under `<cli_root>/registry-cache/`), refreshed at most
once an hour, and refuses to serve a clone that's been edited by hand.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from pilot.core.cron_manager import CronManager
from pilot.exceptions import BenchError, CommandError
from pilot.utils import run_command

REGISTRY_URL = "https://github.com/frappe/pilot-marketplace"

_REFRESH_INTERVAL_SECONDS = 60 * 60
_LS_REMOTE_TIMEOUT_SECONDS = 15
_CRON_JOB_KEY = "marketplace-registry-refresh"
_CRON_SCHEDULE = "0 3 * * *"  # once a day, 03:00


class RegistryCache:
    """A shallow, read-only clone of REGISTRY_URL at `cli_root/registry-cache`.

    The refresh-tracking file lives beside the clone, not inside it — a file
    inside the working tree would itself show up as untracked and permanently
    trip the tamper check.
    """

    def __init__(self, cli_root: Path) -> None:
        self._cli_root = cli_root

    @property
    def path(self) -> Path:
        return self._cli_root / "registry-cache"

    @property
    def apps_v2_path(self) -> Path:
        return self.path / "apps_v2.json"

    @property
    def _last_checked_path(self) -> Path:
        return self._cli_root / "registry-cache.last_checked"

    def ensure_fresh(self) -> None:
        """Clone on first use; otherwise verify the clone hasn't been hand-edited
        and pull if the 1hr refresh window has elapsed."""
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
            raise BenchError(f"Could not clone marketplace registry:\n{exc.message}")
        self.install_daily_refresh_cron()

    def _reject_if_tampered(self) -> None:
        result = run_command(["git", "-C", str(self.path), "status", "--porcelain"])
        if result.stdout.decode().strip():
            raise BenchError(
                "The marketplace registry cache has been modified manually — "
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
            return  # offline — keep serving the existing clone
        local_head = run_command(["git", "-C", str(self.path), "rev-parse", "HEAD"]).stdout.decode().strip()
        if remote_head == local_head:
            return
        run_command(["git", "-C", str(self.path), "fetch", "--depth", "1", "origin", "HEAD"])
        run_command(["git", "-C", str(self.path), "reset", "--hard", "FETCH_HEAD"])

    def _remote_head_sha(self) -> str | None:
        try:
            result = subprocess.run(
                ["git", "ls-remote", REGISTRY_URL, "HEAD"],
                capture_output=True,
                text=True,
                timeout=_LS_REMOTE_TIMEOUT_SECONDS,
            )
        except (subprocess.SubprocessError, OSError):
            return None
        line = result.stdout.splitlines()[0] if result.stdout else ""
        sha = line.split("\t", 1)[0].strip()
        return sha or None

    def _touch_last_checked(self) -> None:
        self._last_checked_path.touch()

    def install_daily_refresh_cron(self) -> None:
        """Idempotently register a system cron entry that runs `ensure_fresh`
        once a day, so the cache stays warm even between get-app/marketplace
        calls. Safe to call repeatedly — CronManager upserts by job key."""
        log_file = self._cli_root / "logs" / "registry-refresh.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        command = f"{sys.executable} -m pilot.core.registry_cache {self._cli_root} >> {log_file} 2>&1"
        CronManager(self._cli_root).set_schedule(_CRON_JOB_KEY, _CRON_SCHEDULE, command)


if __name__ == "__main__":
    # Invoked by the cron entry installed via install_daily_refresh_cron.
    RegistryCache(Path(sys.argv[1])).ensure_fresh()
