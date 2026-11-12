from __future__ import annotations

import time
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request, url_for

from admin.backend.api.responses import created_response, error_response, no_content_response
from admin.backend.internal.session import Session
from admin.backend.internal.two_factor_authentication import (
    MAX_ENROLLED_DEVICES,
    TwoFactorAuthentication,
    TwoFactorError,
)
from admin.backend.middleware import (
    allow_unauthenticated,
    client_ip,
    decode_session_token,
    is_request_authenticated,
    rate_limit,
    set_session_cookie,
)
from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.core.bench.settings.payload import SettingsPayload

auth_bp = Blueprint("auth", __name__)


def _validate_new_password(bench: Bench, new_password: str) -> str | None:
    from pilot.internal.validators import validate_admin_password

    if bench.config.admin.verify_password(new_password):
        return "New password must differ from the current password."
    return validate_admin_password(new_password)


def _second_factor_error(bench: Bench, otp: str):
    """Block the sign-in until an authenticator or recovery code is supplied."""
    two_factor = TwoFactorAuthentication(bench)
    if not two_factor.is_enabled:
        return None
    if not otp:
        # The password was right; the form now needs a code. Not an error the user can fix.
        return jsonify({"authenticated": False, "two_factor_required": True})
    if not two_factor.verify_second_factor(otp):
        return error_response("invalid_otp", "That code is not valid.", 401)
    return None


@auth_bp.get("/session")
@allow_unauthenticated
def get_session():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        bench = Bench(bench_root)
    except Exception:
        if not BenchConfig.exists(bench_root):
            return jsonify({"authenticated": False})
        return error_response(
            "configuration_unavailable",
            "Bench configuration is unavailable.",
            503,
        )
    authenticated = is_request_authenticated(bench)
    response = {"authenticated": authenticated}
    if authenticated:
        response["scope"] = g.jwt_claims.get("scope", "bench")
    return jsonify(response)


@auth_bp.post("/session")
@allow_unauthenticated
@rate_limit(5, 60, user_ip=True)
def create_session():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        bench = Bench(bench_root)
    except Exception:
        return error_response(
            "configuration_unavailable",
            "Bench configuration is unavailable.",
            503,
        )
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    error, redeemed = _validate_login(data, bench)
    if error is not None:
        return error

    # A sign-in link is minted on the server (a setup link, or `pilot start`), so whoever
    # holds one already had shell access. Requiring a device there would only lock out
    # recovery.
    if not redeemed and (blocked := _second_factor_error(bench, str(data.get("otp", "")))):
        return blocked

    if redeemed:
        bench.audit_action("session", {"event": "login_redeemed", "jti": redeemed["jti"]})
    # A redeemed link's session lasts only as long as the link had left; a password
    # login gets the full session.
    ttl = max(60, int(redeemed["exp"]) - int(time.time())) if redeemed else Session.DEFAULT_TTL
    token, jti = Session(bench).issue_session_token(ip=client_ip(), ttl=ttl)
    bench.audit_action(
        "session",
        {
            "event": "issued",
            "jti": jti,
            "scope": "bench",
            "via": "login_link" if redeemed else "password",
        },
    )
    response = created_response(
        {"authenticated": True, "scope": "bench"},
        url_for("auth.get_session"),
    )
    set_session_cookie(
        response,
        token,
        current_app.config["SESSION_COOKIE_SECURE"],
    )
    return response


@auth_bp.delete("/session")
@allow_unauthenticated
def delete_session():
    """Sign out: revoke this session's jti so the token itself stops working, not just
    the cookie in this browser."""
    token = request.cookies.get("sid")
    if token:
        try:
            bench = Bench(Path(current_app.config["BENCH_ROOT"]))
        except Exception:
            bench = None
        if bench:
            claims = decode_session_token(token, bench, client_ip())
            if claims and claims.get("jti"):
                Session(bench).revoke_jti(claims["jti"])
                bench.audit_action("session", {"event": "revoked", "jti": claims["jti"], "via": "logout"})
    response = no_content_response()
    response.delete_cookie("sid")
    return response


def _validate_login(data: dict, bench):
    """Return (error_response_or_None, redeemed_link_claims_or_None)."""
    sid = data.get("sid")
    if sid is not None:
        payload = decode_session_token(sid, bench, client_ip())
        jti = payload.get("jti") if payload else None
        expires = payload.get("exp") if payload else None
        used_logins = current_app.extensions["used_logins"]
        if (
            payload is None
            or not jti
            or not expires
            or payload.get("scope", "bench") != "bench"
            or not used_logins.use(jti, expires)
        ):
            return error_response(
                "invalid_login_token",
                "Invalid or expired sign-in link.",
                401,
            ), None
        return None, payload
    if not bench.config.admin.password:
        return error_response(
            "session_unavailable",
            "No admin password configured in bench.toml",
            503,
        ), None
    if not bench.config.admin.verify_password(str(data.get("password", ""))):
        return error_response("invalid_credentials", "Incorrect password.", 401), None
    return None, None


@auth_bp.post("/password")
@rate_limit(5, 60, user_ip=True)
def change_admin_password():
    """Change the admin password. Leaves existing sessions alone - revoking them is a
    separate, caller-confirmed step."""
    bench_root = Path(current_app.config["BENCH_ROOT"])
    bench = Bench(bench_root)
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)

    new_password = str(data.get("new_password", ""))
    if error := _validate_new_password(bench, new_password):
        return error_response("invalid_password", error, 422)

    with BenchConfig.open(bench_root) as config:
        config.admin.set_password(new_password)

    bench.audit_action("session", {"event": "admin_password_changed"})
    return jsonify({})


def two_factor_payload(bench: Bench) -> dict:
    two_factor = TwoFactorAuthentication(bench)
    return {
        "enabled": two_factor.is_enabled,
        "credentials": two_factor.get_credentials(),
        "recovery_codes_remaining": two_factor.unused_recovery_code_count,
        "max_devices": MAX_ENROLLED_DEVICES,
    }


@auth_bp.get("/two-factor")
def get_two_factor():
    return jsonify(two_factor_payload(Bench(Path(current_app.config["BENCH_ROOT"]))))


@auth_bp.post("/two-factor/enrollment")
@rate_limit(10, 60, user_ip=True)
def start_two_factor_enrollment():
    """Register a device and hand back its setup key, shown only here."""
    bench = Bench(Path(current_app.config["BENCH_ROOT"]))
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    try:
        enrollment = TwoFactorAuthentication(bench).start_enrollment(str(data.get("name", "")))
    except TwoFactorError as error:
        return error_response("invalid_device_name", str(error), 422)
    return jsonify(enrollment)


@auth_bp.post("/two-factor/<name>")
@rate_limit(5, 60, user_ip=True)
def confirm_two_factor_credential(name: str):
    """Activate a device once a code proves its setup key was entered correctly."""
    bench = Bench(Path(current_app.config["BENCH_ROOT"]))
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    two_factor = TwoFactorAuthentication(bench)
    was_enabled = two_factor.is_enabled
    if not two_factor.confirm_enrollment(name, str(data.get("otp", ""))):
        return error_response("invalid_otp", "That code is not valid.", 422)
    bench.audit_action("session", {"event": "two_factor_device_added", "device": name})

    codes = None
    token = None
    if not was_enabled:
        # First device: mint the break-glass codes now, while someone is here to save them.
        codes = two_factor.generate_recovery_codes()
        # Tokens issued before 2FA existed would otherwise skip it until they expired. The
        # caller keeps a session: they just proved possession of the device seconds ago.
        session = Session(bench)
        session.revoke_all()
        token, _ = session.issue_session_token(ip=client_ip())

    payload = two_factor_payload(bench)
    if codes is not None:
        payload["recovery_codes"] = codes
    response = jsonify(payload)
    if token is not None:
        set_session_cookie(response, token, current_app.config["SESSION_COOKIE_SECURE"])
    return response


@auth_bp.delete("/two-factor/<name>")
@rate_limit(5, 60, user_ip=True)
def remove_two_factor_credential(name: str):
    """Remove a device. Dropping the last confirmed one turns 2FA off."""
    bench = Bench(Path(current_app.config["BENCH_ROOT"]))
    two_factor = TwoFactorAuthentication(bench)
    if not two_factor.remove_credential(name):
        return error_response("unknown_credential", "No such device.", 404)
    bench.audit_action(
        "session",
        {
            "event": "two_factor_device_removed",
            "device": name,
            "still_enabled": two_factor.is_enabled,
        },
    )
    return jsonify(two_factor_payload(bench))


@auth_bp.get("/sessions")
def list_sessions():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        config = BenchConfig.read(bench_root)
    except Exception:
        return error_response("settings_unavailable", "Could not read settings.", 500)
    return jsonify(
        {
            "active_tokens": SettingsPayload(config).get_active_tokens(bench_root),
            "current_jti": (getattr(g, "jwt_claims", None) or {}).get("jti"),
        }
    )


@auth_bp.post("/sessions/revoke/all")
@rate_limit(5, 60, user_ip=True)
def revoke_all_sessions():
    """Revoke every other live session and re-issue one for the caller."""
    bench = Bench(Path(current_app.config["BENCH_ROOT"]))
    session = Session(bench)
    revoked = session.revoke_all()
    token, jti = session.issue_session_token(ip=client_ip())
    bench.audit_action(
        "session",
        {"event": "other_sessions_revoked", "jti": jti, "revoked_sessions": revoked},
    )

    response = jsonify({"revoked_sessions": revoked})
    set_session_cookie(response, token, current_app.config["SESSION_COOKIE_SECURE"])
    return response


@auth_bp.post("/sessions/revoke/<jti>")
def revoke_session(jti: str):
    """Revoke an active session by its jti. Requires an authenticated bench session."""
    bench = Bench(Path(current_app.config["BENCH_ROOT"]))
    if not Session(bench).revoke_jti(jti):
        return error_response("unknown_session", "No such active session.", 404)
    bench.audit_action("session", {"event": "revoked", "jti": jti})
    return no_content_response()


@auth_bp.post("/two-factor/recovery-codes")
@rate_limit(5, 60, user_ip=True)
def regenerate_recovery_codes():
    """Replace the whole set. Any code saved from the previous set stops working."""
    bench = Bench(Path(current_app.config["BENCH_ROOT"]))
    codes = TwoFactorAuthentication(bench).generate_recovery_codes()
    bench.audit_action("session", {"event": "recovery_codes_regenerated"})
    return jsonify({"recovery_codes": codes})
