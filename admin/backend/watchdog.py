from __future__ import annotations

import os
import signal
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from flask import Flask

from pilot.tasks.manager.activity import TaskActivityReader
from admin.backend.timing import WATCHDOG_MAX_POLL_SECONDS


@dataclass(frozen=True)
class AdminProcessOwner:
    pid: int
    parent_owned: bool

    @classmethod
    def parent(cls) -> AdminProcessOwner:
        return cls(os.getppid(), True)

    @classmethod
    def current(cls) -> AdminProcessOwner:
        return cls(os.getpid(), False)

    def terminate(self) -> bool:
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
        app.before_request(self.request_started)
        app.teardown_request(self.request_finished)
        threading.Thread(
            target=self._watch,
            name="bench-admin-idle-watchdog",
            daemon=True,
        ).start()

    def request_started(self) -> None:
        with self._lock:
            self._active_requests += 1
            self._last_request = time.monotonic()

    def request_finished(self, _error=None) -> None:
        with self._lock:
            self._active_requests = max(0, self._active_requests - 1)
            self._last_request = time.monotonic()

    def check_once(self) -> bool:
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
        poll_seconds = min(self._timeout, WATCHDOG_MAX_POLL_SECONDS)
        while True:
            time.sleep(poll_seconds)
            if self.check_once():
                return

    def _requests_are_idle(self) -> bool:
        with self._lock:
            return self._requests_are_idle_locked()

    def _requests_are_idle_locked(self) -> bool:
        return (
            self._active_requests == 0
            and time.monotonic() - self._last_request > self._timeout
        )

    def _tasks_are_active(self) -> bool:
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
    existing = app.extensions.get("bench_admin_idle_watchdog")
    if existing is not None:
        return existing
    watchdog = AdminIdleWatchdog(bench_root, timeout, owner)
    app.extensions["bench_admin_idle_watchdog"] = watchdog
    watchdog.install(app)
    return watchdog
