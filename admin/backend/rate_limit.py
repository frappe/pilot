from __future__ import annotations

import functools
import threading
import time

from flask import jsonify, request


class SlidingWindow:
    """In-memory sliding-window request counter, safe for the admin's single gunicorn worker."""

    def __init__(self, max_hits: int, window: int) -> None:
        self._max = max_hits
        self._window = window
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._hits.get(key, []) if now - t < self._window]
            if len(recent) >= self._max:
                self._hits[key] = recent
                return False
            recent.append(now)
            self._hits[key] = recent
            return True


class UsedTokens:
    """Tracks consumed one-time sign-in token ids; entries self-expire."""

    def __init__(self) -> None:
        self._used: dict[str, float] = {}
        self._lock = threading.Lock()

    def use(self, jti: str, exp: float) -> bool:
        now = time.time()
        with self._lock:
            self._used = {j: e for j, e in self._used.items() if e > now}
            if jti in self._used:
                return False
            self._used[jti] = exp
            return True


def _client_ip() -> str:
    """Client IP from X-Real-IP (nginx) or direct peer."""
    return request.headers.get("X-Real-IP") or request.remote_addr or "unknown"


def rate_limit(attempts: int, seconds: int, user_ip: bool = True):
    """Allow at most ``attempts`` calls per ``seconds``, returning HTTP 429 once exceeded."""

    def decorator(view):
        window = SlidingWindow(attempts, seconds)

        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            if not window.allow(_client_ip() if user_ip else "*"):
                return jsonify({"ok": False, "error": "Too many attempts. Try again later."}), 429
            return view(*args, **kwargs)

        return wrapper

    return decorator
