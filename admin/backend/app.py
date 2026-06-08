from __future__ import annotations

import secrets
from pathlib import Path

from flask import Flask, jsonify, request, send_file, session

from .views.apps import apps_bp
from .views.dashboard import dashboard_bp
from .views.stats import stats_bp
from .views.database import database_bp
from .views.logs import logs_bp
from .views.processes import processes_bp
from .views.setup import setup_bp
from .views.settings import settings_bp
from .views.sites import sites_bp
from .views.tasks import tasks_bp
from .views.updates import updates_bp
from .views.volume import volume_bp
from bench_cli.config.bench_config import BenchConfig
from bench_cli.exceptions import ConfigError

_STATIC_DIR = Path(__file__).parent / "static"
_OPEN_PATHS = {"/api/status", "/api/login", "/api/logout"}


def _wizard_status(bench_root: Path) -> dict:
    import tomllib
    name = bench_root.name
    try:
        with open(bench_root / "bench.toml", "rb") as f:
            name = tomllib.load(f).get("bench", {}).get("name", name)
    except Exception:
        pass
    return {"wizard": True, "name": name, "enabled": True, "authenticated": True}


def create_app(bench_root: Path) -> Flask:
    app = Flask(__name__, static_folder=str(_STATIC_DIR), static_url_path="/static")
    app.config["BENCH_ROOT"] = bench_root
    app.config["TEMPLATES_AUTO_RELOAD"] = False
    app.secret_key = secrets.token_hex(32)

    def _load_config():
        return BenchConfig.from_file(bench_root / "bench.toml")

    def _check_enabled(config: BenchConfig):
        if not config.admin.enabled:
            return jsonify({"error": "Admin is disabled", "enabled": False}), 503
        return None

    def _check_password(config: BenchConfig):
        if not config.admin.password:
            return jsonify({"error": "No admin password configured in bench.toml", "enabled": False}), 503
        if not session.get("authenticated"):
            return jsonify({"error": "Authentication required"}), 401
        return None

    @app.before_request
    def _guard():
        if not request.path.startswith("/api") or request.path in _OPEN_PATHS:
            return None
        if request.path.startswith("/api/setup/"):
            return None
        try:
            config = _load_config()
            return _check_enabled(config) or _check_password(config)
        except Exception as exc:
            return jsonify({"error": str(exc), "enabled": False}), 503

    @app.route("/api/status")
    def api_status():
        initialized = (bench_root / "env" / "bin" / "python").exists()
        try:
            config = BenchConfig.from_file(bench_root / "bench.toml")
        except Exception as exc:
            return jsonify({"enabled": False, "error": str(exc)}), 503
        # Show wizard when bench was never initialized, or when init was
        # interrupted before an admin password was saved.
        if not initialized or not config.admin.password:
            return jsonify(_wizard_status(bench_root))
        return jsonify(
            {
                "enabled": config.admin.enabled,
                "name": config.name,
                "authenticated": bool(session.get("authenticated")),
            }
        )

    @app.route("/api/login", methods=["POST"])
    def api_login():
        try:
            config = BenchConfig.from_file(bench_root / "bench.toml")
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 503
        if not config.admin.password:
            return jsonify({"ok": False, "error": "No admin password configured in bench.toml"}), 503
        data = request.get_json(silent=True) or {}
        if data.get("password") == config.admin.password:
            session["authenticated"] = True
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "Incorrect password"}), 401

    @app.route("/api/logout", methods=["POST"])
    def api_logout():
        session.clear()
        return jsonify({"ok": True})

    app.register_blueprint(setup_bp, url_prefix="/api/setup")
    app.register_blueprint(dashboard_bp, url_prefix="/api")
    app.register_blueprint(apps_bp, url_prefix="/api/apps")
    app.register_blueprint(sites_bp, url_prefix="/api/sites")
    app.register_blueprint(processes_bp, url_prefix="/api/processes")
    app.register_blueprint(logs_bp, url_prefix="/api/logs")
    app.register_blueprint(database_bp, url_prefix="/api/database")
    app.register_blueprint(tasks_bp, url_prefix="/api/tasks")
    app.register_blueprint(settings_bp, url_prefix="/api/settings")
    app.register_blueprint(updates_bp, url_prefix="/api/updates")
    app.register_blueprint(volume_bp, url_prefix="/api/volume")
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
