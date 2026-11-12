from __future__ import annotations

import os
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from flask import Flask

from pilot.managers.task import TaskActivityReader

_WATCHDOG_MAX_POLL_SECONDS = 30.0  # longest single sleep between activity checks


@dataclass(frozen=True)
class AdminProcessOwner:
    pid: int
    parent_owned: bool

    @classmethod
    def parent(cls) -> AdminProcessOwner:
        """The parent process as the owner."""
        return cls(os.getppid(), True)

    @classmethod
    def current(cls) -> AdminProcessOwner:
        """This process as the owner."""
        return cls(os.getpid(), False)

    def terminate(self) -> bool:
        """Stop the owner; return False when it is no longer the same process."""
        if self.parent_owned:
            if os.getppid() != self.pid:
                return False
        elif os.getpid() != self.pid:
            return False
        try:
            os.kill(self.pid, signal.SIGTERM)
            return True
        except (PermissionError, ProcessLookupError):
            return False


class AdminIdleWatchdog:
    def __init__(
        self,
        bench_root: Path,
        timeout: float,
        owner: AdminProcessOwner,
    ) -> None:
        self._activity = TaskActivityReader(bench_root)
        self._timeout = timeout
        self._owner = owner
        self._lock = threading.Lock()
        self._active_requests = 0
        self._last_request = time.monotonic()

    def install(self, app: Flask) -> None:
        """Track requests and start the watch thread."""
        app.before_request(self.request_started)
        app.teardown_request(self.request_finished)
        threading.Thread(
            target=self._watch,
            name="bench-admin-idle-watchdog",
            daemon=True,
        ).start()

    def request_started(self) -> None:
        """Count a started request."""
        with self._lock:
            self._active_requests += 1
            self._last_request = time.monotonic()

    def request_finished(self, _error=None) -> None:
        """Count a finished request."""
        with self._lock:
            self._active_requests = max(0, self._active_requests - 1)
            self._last_request = time.monotonic()

    def check_once(self) -> bool:
        """Stop the Admin when it is idle; return whether it stopped."""
        if not self._requests_are_idle():
            return False
        if self._tasks_are_active():
            return False
        with self._lock:
            if not self._requests_are_idle_locked():
                return False
            if self._tasks_are_active():
                return False
            return self._owner.terminate()

    def _watch(self) -> None:
        """Check for idleness until the Admin stops."""
        poll_seconds = min(self._timeout, _WATCHDOG_MAX_POLL_SECONDS)
        while True:
            time.sleep(poll_seconds)
            if self.check_once():
                return

    def _requests_are_idle(self) -> bool:
        """Whether no request ran for the timeout."""
        with self._lock:
            return self._requests_are_idle_locked()

    def _requests_are_idle_locked(self) -> bool:
        """Whether no request ran for the timeout; the caller holds the lock."""
        return self._active_requests == 0 and time.monotonic() - self._last_request > self._timeout

    def _tasks_are_active(self) -> bool:
        """Whether tasks are active; True when that cannot be read."""
        try:
            return self._activity.read().active
        except Exception:
            return True


def install_idle_watchdog(
    app: Flask,
    bench_root: Path,
    timeout: float,
    owner: AdminProcessOwner,
) -> AdminIdleWatchdog:
    """Install the idle watchdog once and return it."""
    existing = app.extensions.get("bench_admin_idle_watchdog")
    if existing is not None:
        return existing
    watchdog = AdminIdleWatchdog(bench_root, timeout, owner)
    app.extensions["bench_admin_idle_watchdog"] = watchdog
    watchdog.install(app)
    return watchdog
