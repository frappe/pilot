from __future__ import annotations

import json
import logging
import secrets
import time
from typing import TYPE_CHECKING, ClassVar

from admin.backend.internal.jwks_cache import JwksCache
from pilot.config import BenchConfig
from pilot.internal import hs256_jwt
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked

if TYPE_CHECKING:
    from pathlib import Path

    from pilot.core.bench import Bench


class _JtiStore:
    """A private ``{jti: {"exp": int, ...}}`` file, cached in-process by mtime: a cheap
    ``stat()`` decides whether the cached dict still matches disk, so a single gunicorn
    worker handling many threads avoids re-reading and re-parsing on every request while
    still picking up a change written by anyone else (another worker, a restart).

    Every store shares that record shape, so subclasses only set FILENAME and add
    whatever extra keys they want to carry next to ``exp``.
    """

    FILENAME: ClassVar[str]
    _cache: ClassVar[dict[Path, tuple[float | None, dict]]] = {}

    def __init__(self, bench: Bench) -> None:
        self._path = bench.path / self.FILENAME

    def add(self, jti: str, exp: int, **fields) -> None:
        """Record a jti until ``exp``. Extra fields ride alongside for stores that keep
        more than an expiry."""
        self._merge({jti: {"exp": int(exp), **fields}})

    def extend(self, entries: dict[str, int]) -> None:
        """Add many ``{jti: exp}`` entries in one write."""
        if entries:
            self._merge({jti: {"exp": int(exp)} for jti, exp in entries.items()})

    def all(self) -> dict:
        with exclusive_file_lock(self._path):
            return dict(self._entries())

    def __contains__(self, jti: str) -> bool:
        with exclusive_file_lock(self._path):
            return jti in self._entries()

    def discard(self, *jtis: str) -> None:
        """Forget these jtis. Absent ones are ignored, and cost no write."""
        with exclusive_file_lock(self._path):
            entries = self._entries()
            if not any(jti in entries for jti in jtis):
                return
            for jti in jtis:
                entries.pop(jti, None)
            self._persist(entries)

    def _merge(self, records: dict[str, dict]) -> None:
        """Fold ``records`` into the file under one lock, pruning what has expired."""
        with exclusive_file_lock(self._path):
            entries = self._entries()
            entries.update(records)
            self._persist(entries)

    def _persist(self, entries: dict) -> None:
        """Prune and write. Callers hold ``exclusive_file_lock``."""
        entries = self._prune(entries)
        replace_private_text_locked(self._path, json.dumps(entries))
        self._cache[self._path] = (self._mtime(), entries)

    def _entries(self) -> dict:
        """This file's entries, from cache if the file hasn't changed since, else disk.

        Re-pruned against the current time either way, so entries that expired since the
        last write still disappear even without a fresh disk read (pruning is a cheap
        dict filter - it's the read/parse that the cache is skipping)."""
        mtime = self._mtime()
        cached = self._cache.get(self._path)
        raw = cached[1] if cached is not None and cached[0] == mtime else self._load_raw()
        entries = self._prune(raw)
        if len(entries) != len(raw):  # garbage-collect expired entries from disk too
            replace_private_text_locked(self._path, json.dumps(entries))
            mtime = self._mtime()
        self._cache[self._path] = (mtime, entries)
        return entries

    def _mtime(self) -> float | None:
        try:
            return self._path.stat().st_mtime
        except FileNotFoundError:
            return None

    def _load_raw(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _prune(entries: dict) -> dict:
        """Live records only. Every store holds ``{jti: {"exp": int, ...}}``; subclasses
        just carry extra keys alongside ``exp``."""
        now = int(time.time())
        live = {}
        for jti, record in entries.items():
            # Files written before the record shape existed held a bare exp int. Read
            # them rather than dropping them: a forgotten revocation is a live token.
            if isinstance(record, int):
                record = {"exp": record}
            if isinstance(record, dict) and isinstance(record.get("exp"), int) and record["exp"] > now:
                live[jti] = record
        return live


class ActiveTokens(_JtiStore):
    """Live sessions this bench has issued or verified, each with its most recent IP and
    last-seen time - refreshed on every request."""

    FILENAME = ".active-jtis.json"


class RevokedTokens(_JtiStore):
    """Token jtis revoked before their expiry; checked on every verification."""

    FILENAME = ".revoked-jtis.json"


class Session:
    """Issues and verifies a single bench's session tokens.

    Locally issued tokens are HS256, signed with the bench's stored secret. Remotely
    issued tokens are verified against the issuer's keys in the host's JWKS cache.
    """

    DEFAULT_TTL = 24 * 3600
    LOGIN_TTL = 5 * 60
    # The setup link, and so the session redeeming it, live 3h - shorter than a password
    # login because the link travels in a URL, over plain http.
    SETUP_SESSION_TTL = 3 * 3600

    # Asymmetric only: a published JWKS public key must never be accepted as an HMAC secret.
    _JWKS_ALGORITHMS: ClassVar[list[str]] = [
        "RS256",
        "RS384",
        "RS512",
        "ES256",
        "ES384",
        "ES512",
        "PS256",
        "PS384",
        "PS512",
        "EdDSA",
    ]

    def __init__(self, bench: Bench) -> None:
        self.bench = bench

    @property
    def admin_config(self):
        return self.bench.config.admin

    def ensure_jwt_secret(self) -> str:
        """Return this bench's JWT secret, generating and persisting one if absent."""
        if not self.admin_config.jwt_secret:
            with BenchConfig.open(self.bench.path, mode="rw") as config:
                if not config.admin.jwt_secret:
                    config.admin.jwt_secret = secrets.token_urlsafe(32)
                self.admin_config.jwt_secret = config.admin.jwt_secret
        return self.admin_config.jwt_secret

    def issue_session_token(
        self, scope: str = "bench", site: str | None = None, ttl: int = DEFAULT_TTL, ip: str = "unknown"
    ) -> tuple[str, str]:
        """Mint an admin session token (with a jti) and register it as active."""
        jti = secrets.token_urlsafe(16)
        token = self._encode(ttl=ttl, scope=scope, jti=jti, site=site)
        ActiveTokens(self.bench).add(jti, int(time.time()) + ttl, ip=ip, last_seen=int(time.time()))
        return token, jti

    def issue_login_token(self) -> str:
        """A short-lived, single-use token for the ?sid= sign-in link."""
        return self._encode(ttl=self.LOGIN_TTL, scope="bench", jti=secrets.token_urlsafe(8))

    def issue_setup_link_token(self) -> str:
        """The ?sid= link that opens the setup wizard on a bench nobody has signed in to.
        Its exp is the session's lifetime: redeeming it inherits the link's remaining life."""
        return self._encode(ttl=self.SETUP_SESSION_TTL, scope="bench", jti=secrets.token_urlsafe(8))

    def issue_site_token(self, site: str, ttl: int = DEFAULT_TTL) -> str:
        """A token scoped to a single site for site-to-bench API calls."""
        if not site:
            raise ValueError("Site name is required.")
        return self._encode(ttl=ttl, scope="site", site=site)

    def verify_token(self, token: str, ip: str = "unknown") -> dict | None:
        """Verify a token: local HS256 first, then the bench's JWKS keys if configured.

        A token whose jti has been revoked is rejected. Otherwise its entry in the active
        tracker is refreshed with this request's IP and time.
        """
        claims = self._decode(token)
        if claims is None:
            logging.warning("Rejected unknown or invalid session token from %s", ip)
            return None
        jti, exp = claims.get("jti"), claims.get("exp")
        if jti:
            if jti in RevokedTokens(self.bench):
                return None
            if exp:
                ActiveTokens(self.bench).add(jti, exp, ip=ip, last_seen=int(time.time()))
        return claims

    @staticmethod
    def has_scope(claims: dict | None, site: str) -> bool:
        if not claims:
            return False
        scope = claims.get("scope")
        if scope == "bench":
            return True
        return scope == "site" and claims.get("site") == site

    def revoke_jti(self, jti: str) -> bool:
        """Revoke an active session by its jti, using its tracked expiry.

        Returns False when the jti is not a known active session (nothing to revoke).
        """
        active = ActiveTokens(self.bench)
        record = active.all().get(jti)
        if record is None:
            return False
        RevokedTokens(self.bench).add(jti, record["exp"])
        active.discard(jti)
        return True

    def revoke_all(self) -> int:
        """Revoke every live session. Returns how many were revoked."""
        live = self.active_jtis()
        RevokedTokens(self.bench).extend(live)
        ActiveTokens(self.bench).discard(*live)
        return len(live)

    def active_jtis(self) -> dict[str, int]:
        """Live session jtis mapped to their expiry. Revoking removes the entry, so
        whatever the tracker still holds is live."""
        return {jti: record["exp"] for jti, record in ActiveTokens(self.bench).all().items()}

    def active_sessions(self) -> dict[str, dict]:
        """Live sessions mapped to their exp/ip/last_seen."""
        return ActiveTokens(self.bench).all()

    def _decode(self, token: str) -> dict | None:
        """Signature/expiry-checked claims: local HS256, then JWKS if configured."""
        claims = self._decode_local(token)
        if claims is None and self.admin_config.jwks_url:
            claims = self._decode_jwks(token)
        return claims

    def _encode(self, ttl: int, scope: str, jti: str | None = None, site: str | None = None) -> str:
        now = int(time.time())
        payload = {"sub": "admin", "iat": now, "exp": now + ttl, "scope": scope}
        if jti:
            payload["jti"] = jti
        if site:
            payload["site"] = site
        return hs256_jwt.encode(payload, self.ensure_jwt_secret())

    def _decode_local(self, token: str) -> dict | None:
        import jwt

        secret = self.admin_config.jwt_secret
        if not token or not secret:
            return None
        try:
            return jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp"]})
        except jwt.InvalidTokenError:
            return None

    def _decode_jwks(self, token: str) -> dict | None:
        import jwt

        url, audience = self.admin_config.jwks_url, self.admin_config.jwks_audience
        if not token or not url or not audience:
            return None
        try:
            kid = jwt.get_unverified_header(token).get("kid")
            if not isinstance(kid, str):
                return None
            signing_key = JwksCache(self.bench.path.parent, url).signing_key(kid)
            if signing_key is None:
                return None
            return jwt.decode(
                token,
                signing_key.key,
                algorithms=self._JWKS_ALGORITHMS,
                audience=audience,
                options={"require": ["exp", "aud"], "verify_aud": True},
            )
        except jwt.PyJWTError:
            return None
