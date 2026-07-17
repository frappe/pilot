from __future__ import annotations

import signal
import threading
from pathlib import Path

import pytest

import pilot.tasks.manager.worker_registry as registry_module
from pilot.tasks.manager.worker_registry import WorkerRegistry


def test_registry_keeps_one_live_worker_per_bench(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = []

    class Worker:
        def __init__(self, bench_root: Path) -> None:
            self.bench_root = bench_root
            self.started = False
            self.woken = False
            self.draining = False
            created.append(self)

        def start(self) -> None:
            self.started = True

        def is_alive(self) -> bool:
            return self.started

        def wake(self) -> None:
            self.woken = True

        def request_drain(self) -> None:
            self.draining = True

    monkeypatch.setattr(registry_module, "TaskWorker", Worker)
    registry = WorkerRegistry()

    first = registry.start(tmp_path)
    second = registry.start(tmp_path)

    assert first is second
    assert len(created) == 1
    assert registry.wake(tmp_path) is True
    assert first.woken is True
    registry.request_drain()
    assert first.draining is True


def test_signal_handler_requests_drain_before_previous_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = []
    handlers = {}
    registry = WorkerRegistry()
    monkeypatch.setattr(registry, "request_drain", lambda: events.append("drain"))
    monkeypatch.setattr(
        signal,
        "getsignal",
        lambda signum: lambda received, frame: events.append(("previous", received)),
    )
    monkeypatch.setattr(
        signal,
        "signal",
        lambda signum, handler: handlers.setdefault(signum, handler),
    )

    registry.install_signal_handlers()
    handlers[signal.SIGTERM](signal.SIGTERM, None)

    assert events == ["drain", ("previous", signal.SIGTERM)]


def test_signal_handler_preserves_default_process_termination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = []
    handlers = {}
    registry = WorkerRegistry()
    monkeypatch.setattr(registry, "request_drain", lambda: events.append("drain"))
    monkeypatch.setattr(signal, "getsignal", lambda signum: signal.SIG_DFL)
    monkeypatch.setattr(
        signal,
        "signal",
        lambda signum, handler: handlers.__setitem__(signum, handler),
    )
    monkeypatch.setattr(registry_module.os, "getpid", lambda: 4321)
    killed = threading.Event()

    def kill(pid, signum):
        events.append(("kill", pid, signum))
        killed.set()

    monkeypatch.setattr(
        registry_module.os,
        "kill",
        kill,
    )

    registry.install_signal_handlers()
    handlers[signal.SIGTERM](signal.SIGTERM, None)

    assert killed.wait(1)
    assert events == ["drain", ("kill", 4321, signal.SIGTERM)]
    assert handlers[signal.SIGTERM] == signal.SIG_DFL
