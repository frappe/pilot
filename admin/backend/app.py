from __future__ import annotations

import hmac
import os
import signal
import threading
import time
from pathlib import Path

from flask import Flask, g, jsonify, request, send_file

from .rate_limit import rate_limit, UsedTokens
from .views.apps import apps_bp
from .views.benches import benches_bp
from .views.dashboard import dashboard_bp
from .views.database import database_bp
from .views.git import git_bp
from .views.logs import logs_bp
from .views.processes import processes_bp
from .views.settings import settings_bp
from .views.setup import setup_bp, wizard_marker_path
from .views.sites import sites_bp
from .views.ssh_keys import ssh_keys_bp
from .views.stats import stats_bp
from .views.tasks import tasks_bp
from .views.updates import updates_bp
from pilot.config.bench_config import BenchConfig
from pilot.config.toml_store import BenchTomlStore
from pilot.exceptions import ConfigError

_STATIC_DIR = Path(__file__).parent / "static"
_OPEN_PATHS = {"/api/status", "/api/login", "/api/logout", "/api/ping"}


def _wizard_status(bench_root: Path) -> dict:
    name = bench_root.name
    try:
        name = BenchTomlStore.for_bench(bench_root).read_raw().get("bench", {}).get("name", name)
    except Exception:
        pass
    return {"wizard": True, "name": name, "enabled": True, "authenticated": True}


def _setup_complete(bench_root: Path, config: BenchConfig) -> bool:
    """Whether first-time setup has fully finished."""
    if not (bench_root / "env" / "bin" / "python").exists() or not config.admin.password:
        return False
    if config.production.process_manager and not config.production.enabled:
        return False
    try:
        from admin.backend.tasks.manager.task_reader import TaskReader

        tasks = TaskReader(bench_root).list_tasks(limit=20)
        if any(t.command == "wizard-setup" and t.status == "running" for t in tasks):
            return False
    except Exception:
        pass
    return True


def _install_idle_watchdog(app: Flask) -> None:
    """Stop the admin after a period of inactivity when socket-activated."""
    raw = os.environ.get("BENCH_ADMIN_IDLE_TIMEOUT")
    if not raw:
        return
    timeout = int(raw)
    if timeout <= 0:
        return

    last_request = time.monotonic()
    lock = threading.Lock()

    @app.before_request
    def _touch() -> None:
        nonlocal last_request
        with lock:
            last_request = time.monotonic()

    def _watchdog() -> None:
        while True:
            time.sleep(min(timeout, 30))
            with lock:
                idle = time.monotonic() - last_request
            if idle > timeout:
                os.kill(os.getppid(), signal.SIGTERM)
                return

    threading.Thread(target=_watchdog, daemon=True).start()


def create_app(bench_root: Path) -> Flask:
    app = Flask(__name__, static_folder=str(_STATIC_DIR), static_url_path="/static")
    app.config["BENCH_ROOT"] = bench_root
    app.config["TEMPLATES_AUTO_RELOAD"] = False

    _install_idle_watchdog(app)
    used_logins = UsedTokens()
    config_store = BenchTomlStore.for_bench(bench_root)

    def _load_config():
        return config_store.read()

    def _check_enabled(config: BenchConfig):
        if not config.admin.enabled:
            return jsonify({"error": "Admin is disabled", "enabled": False}), 503
        return None

    def _is_authenticated(config: BenchConfig) -> bool:
        from .auth import decode_session_token

        token = _extract_token()
        if not token:
            return False
        claims = decode_session_token(token, config)
        if claims is None:
            return False
        g.jwt_claims = claims
        return True

    def _extract_token() -> str | None:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:]
        return request.cookies.get("sid")

    def _set_sid_cookie(resp, sid: str, config: BenchConfig):
        resp.set_cookie(
            "sid",
            sid,
            max_age=24 * 3600,
            httponly=True,
            secure=config.production.enabled and config.admin.tls,
            samesite="Lax",
        )

    def _check_password(config: BenchConfig):
        if not config.admin.password:
            return jsonify(
                {"error": "No admin password configured in bench.toml", "enabled": False}
            ), 503
        if not _is_authenticated(config):
            return jsonify({"error": "Authentication required"}), 401
        from .auth import authorization_error

        view = app.view_functions.get(request.endpoint) if request.endpoint else None
        error = authorization_error(g.jwt_claims, view, request.view_args or {})
        if error:
            return jsonify({"error": error}), 403
        return None

    @app.before_request
    def _guard():
        g.jwt_claims = None
        if not request.path.startswith("/api") or request.path in _OPEN_PATHS:
            return None
        is_setup = request.path.startswith("/api/setup/")
        try:
            config = _load_config()
        except Exception as exc:
            if is_setup and not config_store.exists():
                return None
            return jsonify({"error": str(exc), "enabled": False}), 503
        if is_setup and not config.admin.password:
            return None
        return _check_enabled(config) or _check_password(config)

    @app.route("/api/ping")
    def api_ping():
        resp = jsonify({"ok": True})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    @app.route("/api/status")
    def api_status():
        initialized = (bench_root / "env" / "bin" / "python").exists()
        try:
            config = BenchTomlStore.for_bench(bench_root).read()
        except Exception as exc:
            return jsonify({"enabled": False, "error": str(exc)}), 503
        if not initialized or not config.admin.password:
            return jsonify(_wizard_status(bench_root))
        marker = wizard_marker_path(bench_root)
        if marker.exists():
            if _setup_complete(bench_root, config):
                marker.unlink(missing_ok=True)
            else:
                return jsonify(_wizard_status(bench_root))
        from pilot.platform import native_process_manager

        return jsonify(
            {
                "enabled": config.admin.enabled,
                "name": config.name,
                "db_type": config.db_type,
                "production": config.production.enabled,
                "native_process_manager": native_process_manager(),
                "allow_bench_management": config.admin.allow_bench_management,
                "authenticated": _is_authenticated(config),
            }
        )

    @app.route("/api/login", methods=["POST"])
    @rate_limit(5, 60, user_ip=True)
    def api_login():
        try:
            config = BenchTomlStore.for_bench(bench_root).read()
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 503
        if not config.admin.password:
            return jsonify(
                {"ok": False, "error": "No admin password configured in bench.toml"}
            ), 503
        from .auth import decode_session_token
        from pilot.commands.generate_session import ensure_jwt_secret, issue_token

        data = request.get_json(silent=True) or {}
        sid = data.get("sid")
        if sid is not None:
            payload = decode_session_token(sid, config)
            jti = payload.get("jti") if payload else None
            # A ?sid= sign-in must be a single-use (jti), bench-scoped token.
            # Requiring a jti also blocks site-scoped API tokens (which carry
            # none) from being exchanged for a full admin session, and stops a
            # captured token from being replayed for fresh sessions.
            if (
                payload is None
                or not jti
                or payload.get("scope", "bench") != "bench"
                or not used_logins.use(jti, payload["exp"])
            ):
                return jsonify({"ok": False, "error": "Invalid or expired sign-in link"}), 401
        elif not hmac.compare_digest(str(data.get("password", "")), config.admin.password):
            return jsonify({"ok": False, "error": "Incorrect password"}), 401
        resp = jsonify({"ok": True})
        _set_sid_cookie(resp, issue_token(ensure_jwt_secret(bench_root / "bench.toml")), config)
        return resp

    @app.route("/api/logout", methods=["POST"])
    def api_logout():
        resp = jsonify({"ok": True})
        resp.delete_cookie("sid")
        return resp

    app.register_blueprint(setup_bp, url_prefix="/api/setup")
    app.register_blueprint(dashboard_bp, url_prefix="/api")
    app.register_blueprint(apps_bp, url_prefix="/api/apps")
    app.register_blueprint(benches_bp, url_prefix="/api/benches")
    app.register_blueprint(sites_bp, url_prefix="/api/sites")
    app.register_blueprint(processes_bp, url_prefix="/api/processes")
    app.register_blueprint(logs_bp, url_prefix="/api/logs")
    app.register_blueprint(database_bp, url_prefix="/api/database")
    app.register_blueprint(tasks_bp, url_prefix="/api/tasks")
    app.register_blueprint(settings_bp, url_prefix="/api/settings")
    app.register_blueprint(updates_bp, url_prefix="/api/updates")
    app.register_blueprint(git_bp, url_prefix="/api/git")
    app.register_blueprint(ssh_keys_bp, url_prefix="/api/ssh-keys")
    app.register_blueprint(stats_bp, url_prefix="/api")

    app.register_error_handler(ConfigError, _handle_config_error)
    app.register_error_handler(FileNotFoundError, _handle_file_not_found)

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def serve_spa(path):
        dist = _STATIC_DIR / "dist"
        if not dist.exists():
            return "Frontend not built. Run: cd admin/frontend && npm install && npm run build", 503
        candidate = dist / path
        if path and candidate.exists() and candidate.is_file():
            return send_file(str(candidate))
        return send_file(str(dist / "index.html"))

    return app


def _handle_config_error(error: ConfigError):
    return jsonify({"error": str(error)}), 500


def _handle_file_not_found(error: FileNotFoundError):
    return jsonify({"error": str(error)}), 404
