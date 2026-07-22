from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

_HEADER = {"alg": "HS256", "typ": "JWT"}
DEFAULT_TTL = 24 * 3600
LOGIN_TTL = 5 * 60


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(signing_input: str, secret: str) -> bytes:
    return hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()


def issue_token(
    secret: str,
    ttl: int = DEFAULT_TTL,
    issued_at: float | None = None,
    jti: str | None = None,
    scope: str = "bench",
    site: str | None = None,
) -> str:
    if not secret:
        raise ValueError("JWT secret is not configured.")
    now = int(issued_at or time.time())
    payload = {"sub": "admin", "iat": now, "exp": now + ttl, "scope": scope}
    if jti:
        payload["jti"] = jti
    if site:
        payload["site"] = site
    body = ".".join(_b64(json.dumps(part, separators=(",", ":")).encode()) for part in (_HEADER, payload))
    return f"{body}.{_b64(_sign(body, secret))}"


def decode_token(token: str, secret: str) -> dict | None:
    """Return the token's claims if its signature is valid and it has not
    expired, else None."""
    if not token or not secret:
        return None
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        if not hmac.compare_digest(_unb64(signature_b64), _sign(f"{header_b64}.{payload_b64}", secret)):
            return None
        payload = json.loads(_unb64(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None
    exp = payload.get("exp")
    return payload if isinstance(exp, int) and time.time() < exp else None


def is_token_valid(token: str, secret: str) -> bool:
    return decode_token(token, secret) is not None


def has_scope(claims: dict | None, site: str) -> bool:
    if not claims:
        return False
    token_scope = claims.get("scope")
    if token_scope == "bench":
        return True
    if token_scope == "site":
        return claims.get("site") == site
    return False


def issue_login_token(secret: str) -> str:
    """A short-lived, single-use token for the ?sid= sign-in link."""
    return issue_token(secret, ttl=LOGIN_TTL, jti=secrets.token_urlsafe(8), scope="bench")


def issue_site_token(secret: str, site: str, ttl: int = DEFAULT_TTL) -> str:
    """A token scoped to a single site for site-to-bench API calls."""
    if not site:
        raise ValueError("Site name is required.")
    return issue_token(secret, ttl=ttl, scope="site", site=site)


def ensure_jwt_secret(toml_path) -> str:
    from pilot.config import BenchConfig

    with BenchConfig.open(toml_path, mode="raw") as data:
        secret = data.get("admin", {}).get("jwt_secret")
        if not secret:
            secret = secrets.token_urlsafe(32)
            data.setdefault("admin", {})["jwt_secret"] = secret
        return secret
