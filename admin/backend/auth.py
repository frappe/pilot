from __future__ import annotations

import functools

from flask import g, jsonify


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
