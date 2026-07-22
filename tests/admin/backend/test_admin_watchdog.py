from __future__ import annotations

import signal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from admin.backend.app import configure_idle_watchdog
from admin.backend.watchdog import AdminIdleWatchdog, AdminProcessOwner


class _FakeApp:
    def __init__(self) -> None:
        self.before_request_funcs: list = []
        self.teardown_request_funcs: list = []
        self.extensions: dict = {}
        self.config: dict = {}

    def before_request(self, function):
        self.before_request_funcs.append(function)
        return function

    def teardown_request(self, function):
        self.teardown_request_funcs.append(function)
        return function


def inactive() -> SimpleNamespace:
    return SimpleNamespace(active=False)


def active() -> SimpleNamespace:
    return SimpleNamespace(active=True)


def test_watchdog_noop_without_env(monkeypatch) -> None:
    monkeypatch.delenv("BENCH_ADMIN_IDLE_TIMEOUT", raising=False)
    app = _FakeApp()
    with patch("admin.backend.watchdog.threading.Thread") as thread:
        configure_idle_watchdog(app, Path("/bench"))
    thread.assert_not_called()
    assert app.before_request_funcs == []


def test_watchdog_noop_when_timeout_not_positive(monkeypatch) -> None:
    monkeypatch.setenv("BENCH_ADMIN_IDLE_TIMEOUT", "0")
    app = _FakeApp()
    with patch("admin.backend.watchdog.threading.Thread") as thread:
        configure_idle_watchdog(app, Path("/bench"))
    thread.assert_not_called()


def test_watchdog_registers_request_lifecycle_and_thread(monkeypatch) -> None:
    monkeypatch.setenv("BENCH_ADMIN_IDLE_TIMEOUT", "60")
    app = _FakeApp()
    with patch("admin.backend.watchdog.threading.Thread") as thread:
        configure_idle_watchdog(app, Path("/bench"))

    assert len(app.before_request_funcs) == 1
    assert len(app.teardown_request_funcs) == 1
    thread.assert_called_once()
    assert thread.call_args.kwargs["daemon"] is True


def test_watchdog_rechecks_request_and_task_activity_before_shutdown() -> None:
    owner = AdminProcessOwner(4242, True)
    watchdog = AdminIdleWatchdog(Path("/bench"), 60, owner)
    activities = iter([inactive(), active()])
    watchdog._activity.read = lambda: next(activities)

    with (
        patch("admin.backend.watchdog.time.monotonic", return_value=1000),
        patch("admin.backend.watchdog.os.kill") as kill,
    ):
        watchdog._last_request = 0
        assert watchdog.check_once() is False

    kill.assert_not_called()


def test_watchdog_waits_for_active_request_to_finish() -> None:
    owner = AdminProcessOwner(4242, True)
    watchdog = AdminIdleWatchdog(Path("/bench"), 60, owner)
    watchdog._activity.read = inactive

    with (
        patch("admin.backend.watchdog.time.monotonic", return_value=1000),
        patch("admin.backend.watchdog.os.getppid", return_value=4242),
        patch("admin.backend.watchdog.os.kill") as kill,
    ):
        watchdog._last_request = 0
        watchdog.request_started()
        assert watchdog.check_once() is False
        watchdog.request_finished()
        watchdog._last_request = 0
        assert watchdog.check_once() is True

    kill.assert_called_once_with(4242, signal.SIGTERM)


def test_watchdog_does_not_signal_when_parent_ownership_changes() -> None:
    watchdog = AdminIdleWatchdog(
        Path("/bench"),
        60,
        AdminProcessOwner(4242, True),
    )
    watchdog._activity.read = inactive

    with (
        patch("admin.backend.watchdog.time.monotonic", return_value=1000),
        patch("admin.backend.watchdog.os.getppid", return_value=9999),
        patch("admin.backend.watchdog.os.kill") as kill,
    ):
        watchdog._last_request = 0
        assert watchdog.check_once() is False

    kill.assert_not_called()
