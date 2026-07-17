from __future__ import annotations

import threading
import time


class SlidingWindow:
    """In-memory sliding-window request counter, safe for a single-process server."""

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
