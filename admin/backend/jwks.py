"""Verify JWTs minted by a remote issuer against its JWKS endpoint.

Lets a remote control plane sign session tokens with its own private key; the
bench trusts them by fetching the matching public keys from ``admin.jwks_url``
— no shared secret. Runs in the admin venv, so it leans on PyJWT (and thus
supports RSA, EC, and EdDSA keys) rather than hand-rolled crypto."""

from __future__ import annotations

import jwt
from jwt import PyJWKClient

# Asymmetric algorithms an issuer may sign with; symmetric HS* is deliberately
# excluded so a published public key can never be replayed as an HMAC secret.
_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256", "PS384", "PS512", "EdDSA"]

_clients: dict[str, PyJWKClient] = {}


def verify_jwks_token(token: str, jwks_url: str) -> dict | None:
    """Return the token's claims if a key from the JWKS endpoint verifies it and
    it has not expired, else None. Fails closed on any error."""
    if not token or not jwks_url:
        return None
    try:
        signing_key = _client(jwks_url).get_signing_key_from_jwt(token)
        return jwt.decode(token, signing_key.key, algorithms=_ALGORITHMS, options={"verify_aud": False})
    except jwt.PyJWTError:  # PyJWKClientError (fetch/kid failures) subclasses this too
        return None


def _client(jwks_url: str) -> PyJWKClient:
    """One client per URL — PyJWKClient caches the key set and refreshes it on a
    kid miss, so key rotation just works."""
    client = _clients.get(jwks_url)
    if client is None:
        client = PyJWKClient(jwks_url)
        _clients[jwks_url] = client
    return client
