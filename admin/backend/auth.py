from __future__ import annotations

import functools

from flask import g, jsonify


def decode_session_token(token: str, config) -> dict | None:
    """Validate a session token against the local HS256 secret and, failing
    that, the trusted remote JWKS keys. Returns the token's claims or None."""
    from pilot.commands.generate_session import decode_token

    claims = decode_token(token, config.admin.jwt_secret)
    if claims is not None:
        return claims
    if config.admin.jwks_url:
        from .jwks import verify_jwks_token

        return verify_jwks_token(token, config.admin.jwks_url)
    return None


def require_scope(site):
    """Allow or reject the request based on the JWT ``scope`` claim."""
    from pilot.commands.generate_session import has_scope

    if callable(site):
        resolve = site
    else:
        def resolve(kwargs):
            return site

    def decorator(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            claims = getattr(g, "jwt_claims", None)
            if not has_scope(claims, resolve(kwargs)):
                return jsonify({"error": "Not authorized for this site"}), 403
            return view(*args, **kwargs)

        return wrapper

    return decorator


def current_site_scope() -> str | None:
    """Return the ``site`` claim from the current JWT, or None."""
    claims = getattr(g, "jwt_claims", None)
    if not claims:
        return None
    return claims.get("site")
