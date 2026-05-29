from __future__ import annotations

from pathlib import Path

from flask import Flask, jsonify, request, send_file

from .views.apps import apps_bp
from .views.dashboard import dashboard_bp
from .views.stats import stats_bp
from .views.database import database_bp
from .views.logs import logs_bp
from .views.processes import processes_bp
from .views.sites import sites_bp
from .views.tasks import tasks_bp
from .views.volume import volume_bp
from bench_cli.config.bench_config import BenchConfig
from bench_cli.exceptions import ConfigError

_STATIC_DIR = Path(__file__).parent / "static"


def create_app(bench_root: Path) -> Flask:
    app = Flask(__name__, static_folder=str(_STATIC_DIR), static_url_path="/static")
    app.config["BENCH_ROOT"] = bench_root
    app.config["TEMPLATES_AUTO_RELOAD"] = False

    @app.before_request
    def _check_admin_enabled():
        if not request.path.startswith("/api"):
            return None
        if request.path == "/api/status":
            return None
        try:
            config = BenchConfig.from_file(bench_root / "bench.toml")
            if not config.admin.enabled:
                return jsonify({"error": "Admin is disabled", "enabled": False}), 503
        except Exception as exc:
            return jsonify({"error": str(exc), "enabled": False}), 503

    @app.route("/api/status")
    def api_status():
        try:
            config = BenchConfig.from_file(bench_root / "bench.toml")
            return jsonify({"enabled": config.admin.enabled, "name": config.name})
        except Exception as exc:
            return jsonify({"enabled": False, "error": str(exc)}), 503

    app.register_blueprint(dashboard_bp, url_prefix="/api")
    app.register_blueprint(apps_bp, url_prefix="/api/apps")
    app.register_blueprint(sites_bp, url_prefix="/api/sites")
    app.register_blueprint(processes_bp, url_prefix="/api/processes")
    app.register_blueprint(logs_bp, url_prefix="/api/logs")
    app.register_blueprint(database_bp, url_prefix="/api/database")
    app.register_blueprint(tasks_bp, url_prefix="/api/tasks")
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
