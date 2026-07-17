from __future__ import annotations

import functools
import ipaddress
import threading
from enum import StrEnum

from flask import Flask, current_app, g, request

from pilot.config.toml_store import BenchTomlStore

from admin.backend.api.responses import error_response
from admin.backend.api.routes import is_api_path
from admin.backend.internal.rate_limiter import SlidingWindow

_AUTH_POLICY = "_auth_policy"
_SITE_SCOPE_RESOLVER = "_site_scope_resolver"
_WINDOWS_EXTENSION = "rate_limit_windows"
_WINDOWS_LOCK = threading.Lock()


class AuthPolicy(StrEnum):
    AUTHENTICATED = "authenticated"
    OPEN = "open"
    SETUP_CONDITIONAL = "setup-conditional"


def allow_unauthenticated(view):
    setattr(view, _AUTH_POLICY, AuthPolicy.OPEN)
    return view


def allow_during_setup(view):
    setattr(view, _AUTH_POLICY, AuthPolicy.SETUP_CONDITIONAL)
    return view


def get_auth_policy(view) -> AuthPolicy:
    return getattr(view, _AUTH_POLICY, AuthPolicy.AUTHENTICATED)


def authenticate_request(config) -> bool:
    authorization = request.headers.get("Authorization", "")
    token = authorization[7:] if authorization.startswith("Bearer ") else None
    token = token or request.cookies.get("sid")
    if not token:
        return False
    claims = decode_session_token(token, config)
    if claims is None:
        return False
    g.jwt_claims = claims
    return True


def set_session_cookie(response, token: str, secure: bool) -> None:
    response.set_cookie(
        "sid",
        token,
        max_age=24 * 3600,
        httponly=True,
        secure=secure,
        samesite="Lax",
    )


def decode_session_token(token: str, config) -> dict | None:
    """Decode via the local HS256 secret, falling back to remote JWKS keys."""
    from pilot.core.admin_auth import decode_token

    claims = decode_token(token, config.admin.jwt_secret)
    if claims is not None:
        return claims
    if config.admin.jwks_url:
        from admin.backend.internal.jwks import verify_jwks_token

        return verify_jwks_token(token, config.admin.jwks_url, config.admin.jwks_audience)
    return None


def current_site_scope() -> str | None:
    claims = getattr(g, "jwt_claims", None)
    return claims.get("site") if claims else None


def require_scope(site):
    if callable(site):
        resolve = site
    else:

        def resolve(kwargs):
            return site

    def decorator(view):
        setattr(view, _SITE_SCOPE_RESOLVER, resolve)
        return view

    return decorator


def get_authorization_error(claims: dict | None, view, view_args: dict) -> str | None:
    from pilot.core.admin_auth import has_scope

    resolve_site = getattr(view, _SITE_SCOPE_RESOLVER, None)
    if resolve_site is not None:
        return (
            None if has_scope(claims, resolve_site(view_args)) else "Not authorized for this site"
        )
    if claims and claims.get("scope") == "bench":
        return None
    return "Not authorized for this bench"


def install_auth_guard(app: Flask, config_store: BenchTomlStore) -> None:
    """Reject every API request that its endpoint's AuthPolicy doesn't allow."""

    @app.before_request
    def check_auth():
        g.jwt_claims = None
        if not is_api_path(request.path):
            return None

        view = app.view_functions.get(request.endpoint) if request.endpoint else None
        if view is None:
            return None

        policy = get_auth_policy(view)
        if policy == AuthPolicy.OPEN:
            return None

        allowed_before_setup = policy == AuthPolicy.SETUP_CONDITIONAL
        try:
            config = config_store.read()
        except Exception:
            if allowed_before_setup and not config_store.exists():
                return None
            return error_response(
                "configuration_unavailable",
                "Bench configuration is unavailable.",
                503,
                {"enabled": False},
            )

        if allowed_before_setup and not config.admin.password:
            return None
        if not config.admin.enabled:
            return error_response("admin_disabled", "Admin is disabled.", 503, {"enabled": False})
        if not config.admin.password:
            return error_response(
                "session_unavailable", "No admin password is configured.", 503, {"enabled": False}
            )
        if not authenticate_request(config):
            return error_response("authentication_required", "Authentication is required.", 401)

        error = get_authorization_error(g.jwt_claims, view, request.view_args or {})
        return error_response("forbidden", error, 403) if error else None


def client_ip(default: str = "unknown") -> str:
    """Forwarded client IP, but only when the immediate peer is trusted."""
    peer = request.remote_addr or ""
    trusted_peers = current_app.config.get("TRUSTED_PROXY_PEERS", ())
    if peer in trusted_peers:
        forwarded = request.headers.get("X-Real-IP", "")
        try:
            return str(ipaddress.ip_address(forwarded))
        except ValueError:
            pass
    return peer or default


def rate_limit(attempts: int, seconds: int, user_ip: bool = True):
    """Allow at most `attempts` calls per `seconds` for this view, else respond 429."""

    def decorator(view):
        @functools.wraps(view)
        def wrapper(*args, **kwargs):
            window = _get_window(wrapper, attempts, seconds)
            if not window.allow(client_ip() if user_ip else "*"):
                return error_response(
                    "rate_limit_exceeded",
                    "Too many attempts. Try again later.",
                    429,
                )
            return view(*args, **kwargs)

        return wrapper

    return decorator


def _get_window(view, attempts: int, seconds: int) -> SlidingWindow:
    """One SlidingWindow per (Flask app, decorated view), created on first use."""
    app = current_app._get_current_object()  # type: ignore[attr-defined]  # unwrap Flask's LocalProxy
    with _WINDOWS_LOCK:
        windows = app.extensions.setdefault(_WINDOWS_EXTENSION, {})
        return windows.setdefault(view, SlidingWindow(attempts, seconds))
