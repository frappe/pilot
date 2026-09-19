from __future__ import annotations

import functools
import ipaddress
import threading
from enum import StrEnum

from flask import Flask, current_app, g, request

from admin.backend.api.responses import error_response
from admin.backend.api.routes import is_api_path
from admin.backend.internal.rate_limiter import SlidingWindow
from pilot.core.bench import Bench

_AUTH_POLICY = "_auth_policy"
_SITE_SCOPE_RESOLVER = "_site_scope_resolver"
_WINDOWS_EXTENSION = "rate_limit_windows"
_WINDOWS_LOCK = threading.Lock()


class AuthPolicy(StrEnum):
    AUTHENTICATED = "authenticated"
    OPEN = "open"


def allow_unauthenticated(view):
    setattr(view, _AUTH_POLICY, AuthPolicy.OPEN)
    return view


def get_auth_policy(view) -> AuthPolicy:
    return getattr(view, _AUTH_POLICY, AuthPolicy.AUTHENTICATED)


def is_request_authenticated(bench) -> bool:
    authorization = request.headers.get("Authorization", "")
    token = authorization[7:] if authorization.startswith("Bearer ") else None
    token = token or request.cookies.get("sid")
    if not token:
        return False
    claims = decode_session_token(token, bench, client_ip())
    if claims is None:
        return False
    g.jwt_claims = claims
    g.jwt_token = token
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


def decode_session_token(token: str, bench, ip: str = "unknown") -> dict | None:
    """Verify a token via Session (local HS256, then the bench's remote JWKS keys)."""
    from admin.backend.internal.session import Session

    return Session(bench).verify_token(token, ip)


def is_bench_scoped() -> bool:
    """Whether this request holds a bench session rather than a single site's token."""
    claims = getattr(g, "jwt_claims", None) or {}
    return claims.get("scope") == "bench"


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


def get_authorization_error(claims: dict | None, view, view_args: dict, bench, token: str = "") -> str | None:
    from admin.backend.internal.session import Session

    resolve_site = getattr(view, _SITE_SCOPE_RESOLVER, None)
    if resolve_site is not None:
        site = resolve_site(view_args)
        return None if Session(bench).has_scope(claims, site, token) else "Not authorized for this site"
    if claims and claims.get("scope") == "bench":
        return None
    return "Not authorized for this bench"


def install_auth_guard(app: Flask, bench_root) -> None:
    @app.before_request
    def check_auth():
        g.jwt_claims = None
        g.jwt_token = None
        return _check_auth_request(app, bench_root)


def _check_auth_request(app: Flask, bench_root):
    if not is_api_path(request.path):
        return None

    view = app.view_functions.get(request.endpoint) if request.endpoint else None
    if view is None:
        return None

    if get_auth_policy(view) == AuthPolicy.OPEN:
        return None

    bench, response = _auth_config(bench_root)
    if response is not None:
        return response

    response = _admin_session_response(bench)
    if response is not None:
        return response

    response = _resolve_site_route(bench)
    if response is not None:
        return response

    error = get_authorization_error(
        g.jwt_claims,
        view,
        request.view_args or {},
        bench,
        g.jwt_token or "",
    )
    return error_response("forbidden", error, 403) if error else None


def _resolve_site_route(bench):
    if request.blueprint != "sites" or not request.view_args:
        return None
    name = request.view_args.get("name")
    if not name:
        return None
    try:
        resolved = bench.resolve_site_name(name)
    except Exception:
        return error_response(
            "configuration_unavailable",
            "Site configuration is unavailable.",
            503,
        )
    if resolved:
        request.view_args["name"] = resolved
    return None


def _auth_config(bench_root):
    try:
        return Bench(bench_root), None
    except Exception:
        return None, error_response(
            "configuration_unavailable",
            "Bench configuration is unavailable.",
            503,
            {"enabled": False},
        )


def _admin_session_response(bench):
    if not bench.config.admin.enabled:
        return error_response("admin_disabled", "Admin is disabled.", 503, {"enabled": False})
    if not is_request_authenticated(bench):
        return error_response("authentication_required", "Authentication is required.", 401)
    return None


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


def request_audit_context() -> dict:
    """Audit context for the current request: caller IP and session jti (actor).

    Registered as the audit-log context provider by ``create_app``; returns nothing
    outside a request context, so tasks and the CLI record no actor.
    """
    from flask import has_request_context

    if not has_request_context():
        return {}
    claims = getattr(g, "jwt_claims", None) or {}
    return {"ip": client_ip(), "actor_jti": claims.get("jti"), "actor": claims.get("user.email")}


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
