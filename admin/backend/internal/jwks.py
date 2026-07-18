"""Verify remote JWTs against admin.jwks_url."""

from __future__ import annotations

import jwt
from jwt import PyJWKClient

# Exclude HS*: a public JWKS key must never become an HMAC secret.
_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512", "EdDSA"]

_clients: dict[str, PyJWKClient] = {}


def verify_jwks_token(token: str, jwks_url: str, audience: str) -> dict | None:
    """Return verified claims, or None on any auth failure."""
    if not token or not jwks_url or not audience:
        return None
    try:
        kid = jwt.get_unverified_header(token).get("kid")
        if not isinstance(kid, str):
            return None
        # Unknown kids must not trigger attacker-controlled refetches.
        signing_key = PyJWKClient.match_kid(_client(jwks_url).get_signing_keys(), kid)
        if signing_key is None:
            return None
        return jwt.decode(token, signing_key.key, algorithms=_ALGORITHMS, audience=audience,
                          options={"require": ["exp", "aud"], "verify_aud": True})
    except jwt.PyJWTError:  # PyJWKClientError (fetch failures) subclasses this too
        return None


def _client(jwks_url: str) -> PyJWKClient:
    client = _clients.get(jwks_url)
    if client is None:
        # A real User-Agent; urllib's default is blocked as a bot by Cloudflare
        # and similar WAFs fronting an issuer, which would fail every fetch.
        client = PyJWKClient(jwks_url, headers={"User-Agent": "bench-admin"})
        _clients[jwks_url] = client
    return client
