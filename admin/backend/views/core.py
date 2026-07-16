from __future__ import annotations

import hmac
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request, url_for

from admin.backend.api_contract import created_response, error_response, no_content_response
from admin.backend.auth import (
    allow_unauthenticated,
    authenticate_request,
    decode_session_token,
    set_session_cookie,
)
from admin.backend.rate_limit import rate_limit
from pilot.tasks.manager.activity import TaskActivityReader
from admin.backend.views.setup import wizard_marker_path
from pilot.config.bench_config import BenchConfig
from pilot.config.toml_store import BenchTomlStore
from pilot.internal.atomic_file import exclusive_file_lock
from pilot.platform import native_process_manager

core_bp = Blueprint("core", __name__)


@core_bp.get("/health")
@allow_unauthenticated
def health():
    response = jsonify({"status": "ok"})
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


@core_bp.get("/bootstrap")
@allow_unauthenticated
def bootstrap():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    config_store = BenchTomlStore.for_bench(bench_root)
    try:
        config = config_store.read()
    except Exception:
        if not config_store.exists():
            return jsonify(_setup_bootstrap(bench_root))
        return error_response(
            "configuration_unavailable",
            "Bench configuration is unavailable.",
            503,
        )

    initialized = (bench_root / "env" / "bin" / "python").exists()
    if not initialized or not config.admin.password:
        return jsonify(_setup_bootstrap(bench_root))
    marker = wizard_marker_path(bench_root)
    if marker.exists():
        with exclusive_file_lock(marker):
            if marker.exists():
                handoff_task_id = marker.read_text(encoding="utf-8").strip()
                if not handoff_task_id and _setup_complete(bench_root, config):
                    marker.unlink(missing_ok=True)
                else:
                    return jsonify(_setup_bootstrap(bench_root))
    return jsonify(
        {
            "mode": "admin",
            "enabled": config.admin.enabled,
            "name": config.name,
            "db_type": config.db_type,
            "production": config.production.enabled,
            "native_process_manager": native_process_manager(),
            "allow_bench_management": config.admin.allow_bench_management,
            "task_worker": TaskActivityReader(bench_root).read().public_dict(),
        }
    )


@core_bp.get("/session")
@allow_unauthenticated
def get_session():
    config_store = _config_store()
    try:
        config = config_store.read()
    except Exception:
        if not config_store.exists():
            return jsonify({"authenticated": False})
        return error_response(
            "configuration_unavailable",
            "Bench configuration is unavailable.",
            503,
        )
    authenticated = authenticate_request(config)
    response = {"authenticated": authenticated}
    if authenticated:
        response["scope"] = g.jwt_claims.get("scope", "bench")
    return jsonify(response)


@core_bp.post("/session")
@allow_unauthenticated
@rate_limit(5, 60, user_ip=True)
def create_session():
    config_store = _config_store()
    try:
        config = config_store.read()
    except Exception:
        return error_response(
            "configuration_unavailable",
            "Bench configuration is unavailable.",
            503,
        )
    if not config.admin.password:
        return error_response(
            "session_unavailable",
            "No admin password configured in bench.toml",
            503,
        )
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    error = _validate_login(data, config)
    if error is not None:
        return error

    from pilot.commands.generate_session import ensure_jwt_secret, issue_token

    response = created_response(
        {"authenticated": True, "scope": "bench"},
        url_for("core.get_session"),
    )
    token = issue_token(ensure_jwt_secret(config_store.path))
    set_session_cookie(
        response,
        token,
        current_app.config["SESSION_COOKIE_SECURE"],
    )
    return response


@core_bp.delete("/session")
@allow_unauthenticated
def delete_session():
    response = no_content_response()
    response.delete_cookie("sid")
    return response


def _validate_login(data: dict, config: BenchConfig):
    sid = data.get("sid")
    if sid is not None:
        payload = decode_session_token(sid, config)
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
            )
        return None
    if not hmac.compare_digest(str(data.get("password", "")), config.admin.password):
        return error_response("invalid_credentials", "Incorrect password.", 401)
    return None


def _config_store() -> BenchTomlStore:
    return BenchTomlStore.for_bench(Path(current_app.config["BENCH_ROOT"]))


def _setup_bootstrap(bench_root: Path) -> dict:
    name = bench_root.name
    try:
        raw = BenchTomlStore.for_bench(bench_root).read_raw()
        name = raw.get("bench", {}).get("name", name)
    except Exception:
        pass
    return {"mode": "setup", "name": name, "enabled": True}


def _setup_complete(bench_root: Path, config: BenchConfig) -> bool:
    if not (bench_root / "env" / "bin" / "python").exists():
        return False
    if not config.admin.password:
        return False
    if config.production.process_manager and not config.production.enabled:
        return False
    try:
        from pilot.tasks.manager.task_reader import TaskReader
        from pilot.tasks.manager.task_state import ACTIVE_TASK_STATUSES

        tasks = TaskReader(bench_root).list_tasks(limit=20)
        return not any(
            task.command == "wizard-setup" and task.status in ACTIVE_TASK_STATUSES for task in tasks
        )
    except Exception:
        return True
