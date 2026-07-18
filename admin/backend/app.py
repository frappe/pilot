from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, send_file

from admin.backend.api.errors import install_api_error_handlers
from admin.backend.api.responses import error_response
from admin.backend.api.routes import API_V1_PREFIX
from admin.backend.internal.rate_limiter import UsedTokens
from admin.backend.middleware import allow_unauthenticated, install_auth_guard

STATIC_DIR = Path(__file__).parent / "static"


def create_app(bench_root: Path) -> Flask:
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    app.config["BENCH_ROOT"] = bench_root
    app.config["TEMPLATES_AUTO_RELOAD"] = False
    app.config["TRUSTED_PROXY_PEERS"] = trusted_proxy_peers(bench_root)
    app.config["SESSION_COOKIE_SECURE"] = is_secure_cookie(bench_root)

    app.extensions["used_logins"] = UsedTokens()

    install_auth_guard(app, bench_root)
    register_blueprints(app)
    register_frontend(app)
    install_api_error_handlers(app)

    return app


def register_blueprints(app: Flask) -> None:
    from admin.backend.api.v1.apps import apps_bp, marketplace_bp
    from admin.backend.api.v1.benches import bench_readiness_bp, benches_bp
    from admin.backend.api.v1.core import core_bp
    from admin.backend.api.v1.databases import database_bp
    from admin.backend.api.v1.git import git_bp
    from admin.backend.api.v1.logs import logs_bp
    from admin.backend.api.v1.processes import processes_bp
    from admin.backend.api.v1.settings import audit_bp, network_bp, settings_bp
    from admin.backend.api.v1.setup import setup_bp
    from admin.backend.api.v1.sites import sites_bp
    from admin.backend.api.v1.ssh_keys import ssh_keys_bp
    from admin.backend.api.v1.stats import stats_bp
    from admin.backend.api.v1.tasks import task_worker_bp, tasks_bp
    from admin.backend.api.v1.updates import updates_bp

    app.register_blueprint(core_bp, url_prefix=API_V1_PREFIX)
    app.register_blueprint(setup_bp, url_prefix=f"{API_V1_PREFIX}/setup")
    app.register_blueprint(apps_bp, url_prefix=f"{API_V1_PREFIX}/apps")
    app.register_blueprint(marketplace_bp, url_prefix=f"{API_V1_PREFIX}/marketplace")
    app.register_blueprint(benches_bp, url_prefix=f"{API_V1_PREFIX}/benches")
    app.register_blueprint(bench_readiness_bp, url_prefix=API_V1_PREFIX)
    app.register_blueprint(sites_bp, url_prefix=f"{API_V1_PREFIX}/sites")
    app.register_blueprint(processes_bp, url_prefix=f"{API_V1_PREFIX}/runtime")
    app.register_blueprint(logs_bp, url_prefix=f"{API_V1_PREFIX}/logs")
    app.register_blueprint(database_bp, url_prefix=f"{API_V1_PREFIX}/database")
    app.register_blueprint(tasks_bp, url_prefix=f"{API_V1_PREFIX}/tasks")
    app.register_blueprint(task_worker_bp, url_prefix=API_V1_PREFIX)
    app.register_blueprint(settings_bp, url_prefix=f"{API_V1_PREFIX}/settings")
    app.register_blueprint(audit_bp, url_prefix=API_V1_PREFIX)
    app.register_blueprint(network_bp, url_prefix=API_V1_PREFIX)
    app.register_blueprint(updates_bp, url_prefix=API_V1_PREFIX)
    app.register_blueprint(git_bp, url_prefix=f"{API_V1_PREFIX}/git")
    app.register_blueprint(ssh_keys_bp, url_prefix=f"{API_V1_PREFIX}/ssh-keys")
    app.register_blueprint(stats_bp, url_prefix=API_V1_PREFIX)


def register_frontend(app: Flask) -> None:
    """Serve the built single-page app for every path the API doesn't own."""

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    @allow_unauthenticated
    def serve_frontend(path):
        if path == "api" or path.startswith("api/"):
            return error_response("not_found", "API route not found.", 404)
        dist = STATIC_DIR / "dist"
        if not dist.exists():
            return "Frontend not built. Run: cd admin/frontend && npm install && npm run build", 503
        candidate = dist / path
        if path and candidate.exists() and candidate.is_file():
            return send_file(str(candidate))
        return send_file(str(dist / "index.html"))


def configure_idle_watchdog(
    app: Flask,
    bench_root: Path | None = None,
):
    raw = os.environ.get("BENCH_ADMIN_IDLE_TIMEOUT")
    if not raw:
        return None
    timeout = int(raw)
    if timeout <= 0:
        return None
    from admin.backend.watchdog import (
        AdminProcessOwner,
        install_idle_watchdog,
    )

    root = bench_root or app.config.get("BENCH_ROOT")
    if root is None:
        raise ValueError("Bench root is required for the Admin idle watchdog")
    return install_idle_watchdog(
        app,
        Path(root),
        timeout,
        AdminProcessOwner.parent(),
    )


def trusted_proxy_peers(bench_root: Path) -> tuple[str, ...]:
    """Immediate peers allowed to supply nginx's forwarded client headers."""
    from pilot.config import BenchConfig

    try:
        production_enabled = BenchConfig.read(bench_root).production.enabled
    except Exception:
        production_enabled = False
    if not production_enabled:
        return ()
    # Production nginx reaches the admin over loopback or a Unix socket. An
    # empty REMOTE_ADDR is how the latter is represented by the WSGI server.
    return ("127.0.0.1", "::1", "")


def is_secure_cookie(bench_root: Path) -> bool:
    """Whether the browser reaches Admin over explicitly configured HTTPS."""
    from pilot.config import BenchConfig

    try:
        config = BenchConfig.read(bench_root)
    except Exception:
        return False
    if not config.production.enabled:
        return False
    if config.admin.tls:
        return True

    from pilot.core.adapters.domain_provider import DomainRouteProvider

    try:
        return bool(DomainRouteProvider.proxy_servers())
    except Exception:
        return False
