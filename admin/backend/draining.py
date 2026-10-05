from __future__ import annotations

import signal
import threading

_draining = threading.Event()


def is_draining() -> bool:
    """Whether this worker is stopping. Live streams end then and the browser reconnects."""
    return _draining.is_set()


def drain_streams_on_sigterm() -> None:
    """Event streams never finish on their own, so flag the worker as draining before
    gunicorn's SIGTERM handler waits for open requests."""
    gunicorn_handler = signal.getsignal(signal.SIGTERM)
    if not callable(gunicorn_handler):
        return

    def handle(signum, frame) -> None:
        _draining.set()
        gunicorn_handler(signum, frame)

    signal.signal(signal.SIGTERM, handle)
