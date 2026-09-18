from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from pilot.internal.atomic_file import atomic_write_private_text

if TYPE_CHECKING:
    from jwt import PyJWK, PyJWKSet


class JwksCache:
    """A remote issuer's public keys, kept on disk beside the host-shared config.

    A token whose key is cached verifies without a fetch, and a cache older than a
    minute refreshes in the background. An unknown key forces a fetch, at most once
    per interval, so a caller cannot make every request fetch from the issuer.
    """

    FILENAME = ".jwks-cache.json"
    REFRESH_AFTER_SECONDS = 60
    FORCED_FETCH_INTERVAL_SECONDS = 30

    _lock: ClassVar[threading.Lock] = threading.Lock()
    _refreshing: ClassVar[set[Path]] = set()
    _last_forced_fetch: ClassVar[dict[Path, float]] = {}

    def __init__(self, directory: Path, url: str) -> None:
        self.path = Path(directory) / self.FILENAME
        self.url = url

    def signing_key(self, kid: str) -> PyJWK | None:
        """The key for ``kid``. Fetches only when the cache does not hold it."""
        record = self._read()
        if record is not None and (key := self._find(record, kid)) is not None:
            if self._is_stale(record):
                self.refresh_in_background()
            return key
        if not self._claim_forced_fetch():
            return None
        return self._find(self.refresh(), kid)

    def seed(self, jwks: dict) -> bool:
        """Store a key set delivered with the host's credential. False when it holds no usable key."""
        import jwt

        try:
            self._write(jwks)
        except (jwt.PyJWTError, OSError) as error:
            logging.warning("Ignoring the initial JWKS cache for %s: %s", self.url, error)
            return False
        return True

    def refresh(self) -> dict | None:
        """Fetch and store the issuer's key set. A failed or unusable fetch keeps the old cache."""
        import jwt
        from jwt import PyJWKClient

        # A real User-Agent; urllib's default is blocked as a bot by Cloudflare and
        # similar WAFs fronting an issuer, which would fail every fetch.
        client = PyJWKClient(self.url, headers={"User-Agent": "bench-admin"}, cache_jwk_set=False)
        try:
            self._write(client.fetch_data())
        except (jwt.PyJWTError, OSError, ValueError) as error:
            logging.warning("Could not refresh the JWKS cache from %s: %s", self.url, error)
            return None
        return self._read()

    def refresh_in_background(self) -> None:
        """Start one refresh for this cache, unless one is already running."""
        with self._lock:
            if self.path in self._refreshing:
                return
            self._refreshing.add(self.path)
        threading.Thread(target=self._refresh_once, name="jwks-cache-refresh", daemon=True).start()

    def _refresh_once(self) -> None:
        try:
            self.refresh()
        finally:
            with self._lock:
                self._refreshing.discard(self.path)

    def _claim_forced_fetch(self) -> bool:
        """True when no forced fetch ran for this cache within the interval."""
        now = time.monotonic()
        with self._lock:
            last = self._last_forced_fetch.get(self.path)
            if last is not None and now - last < self.FORCED_FETCH_INTERVAL_SECONDS:
                return False
            self._last_forced_fetch[self.path] = now
            return True

    def _write(self, jwks: dict) -> None:
        """Store ``jwks`` once it parses as a key set with at least one usable key."""
        self._key_set(jwks)
        record = {"url": self.url, "fetched_at": int(time.time()), "jwks": jwks}
        atomic_write_private_text(self.path, json.dumps(record))

    def _read(self) -> dict | None:
        """The cached record for this issuer, or None."""
        try:
            record = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except ValueError:
            logging.warning("Ignoring an unreadable JWKS cache at %s", self.path)
            return None
        if not isinstance(record, dict) or record.get("url") != self.url:
            return None
        return record

    def _find(self, record: dict | None, kid: str) -> PyJWK | None:
        import jwt
        from jwt import PyJWKClient

        if record is None:
            return None
        try:
            return PyJWKClient.match_kid(self._key_set(record.get("jwks")).keys, kid)
        except jwt.PyJWTError:
            return None

    def _is_stale(self, record: dict) -> bool:
        fetched_at = record.get("fetched_at")
        return not isinstance(fetched_at, int) or time.time() - fetched_at > self.REFRESH_AFTER_SECONDS

    @staticmethod
    def _key_set(jwks: object) -> PyJWKSet:
        import jwt

        if not isinstance(jwks, dict):
            raise jwt.PyJWKSetError("The JWK set is not a JSON object.")
        return jwt.PyJWKSet.from_dict(jwks)
