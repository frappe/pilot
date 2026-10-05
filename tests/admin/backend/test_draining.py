from __future__ import annotations

import os
import signal

from admin.backend import draining


def test_sigterm_marks_the_worker_draining_and_still_reaches_gunicorn(monkeypatch) -> None:
    received: list[int] = []
    previous = signal.signal(signal.SIGTERM, lambda signum, frame: received.append(signum))
    monkeypatch.setattr(draining, "_draining", draining.threading.Event())
    try:
        draining.drain_streams_on_sigterm()
        os.kill(os.getpid(), signal.SIGTERM)
    finally:
        signal.signal(signal.SIGTERM, previous)

    assert draining.is_draining()
    assert received == [signal.SIGTERM]


def test_without_gunicorn_handler_nothing_is_installed() -> None:
    previous = signal.signal(signal.SIGTERM, signal.SIG_DFL)
    try:
        draining.drain_streams_on_sigterm()
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    finally:
        signal.signal(signal.SIGTERM, previous)
