from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, g, request, send_file

from .api.errors import (
    API_V1_PREFIX,
    install_api_error_handlers,
    is_api_path,
)
from .api.responses import error_response
from .auth import (
    AuthPolicy,
    allow_unauthenticated,
    authenticate_request,
    endpoint_auth_policy,
)
from .rate_limit import UsedTokens
from .uploads import MAX_RESTORE_UPLOAD_BYTES
from .api.v1.apps import apps_bp, marketplace_bp
from .api.v1.benches import bench_readiness_bp, benches_bp
from .api.v1.core import core_bp
from .api.v1.dashboard import dashboard_bp
from .api.v1.databases import database_bp
from .api.v1.git import git_bp
from .api.v1.logs import logs_bp
from .api.v1.processes import processes_bp
from .api.v1.settings import audit_bp, network_bp, settings_bp
from .api.v1.site_login import site_login_bp
from .api.v1.setup import setup_bp
from .api.v1.sites import site_restores_bp, sites_bp
from .api.v1.ssh_keys import ssh_keys_bp
from .api.v1.stats import stats_bp
from .api.v1.tasks import task_worker_bp, tasks_bp
from .api.v1.updates import updates_bp
from pilot.config.bench_config import BenchConfig
from pilot.config.toml_store import BenchTomlStore
from admin.backend.site_login_handoff import SiteLoginHandoffStore

_STATIC_DIR = Path(__file__).parent / "static"


def _install_idle_watchdog(
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


def create_app(bench_root: Path) -> Flask:
    app = Flask(__name__, static_folder=str(_STATIC_DIR), static_url_path="/static")
    config_store = BenchTomlStore.for_bench(bench_root)
    app.config["BENCH_ROOT"] = bench_root
    app.config["MAX_CONTENT_LENGTH"] = MAX_RESTORE_UPLOAD_BYTES
    app.config["TEMPLATES_AUTO_RELOAD"] = False
    app.config["TRUSTED_PROXY_PEERS"] = _trusted_proxy_peers(config_store)
    app.config["SESSION_COOKIE_SECURE"] = _secure_cookie_setting(config_store)

    app.extensions["used_logins"] = UsedTokens()
    app.extensions["site_login_handoffs"] = SiteLoginHandoffStore()

    def _load_config():
        return config_store.read()

    def _check_enabled(config: BenchConfig):
        if not config.admin.enabled:
            return error_response(
                "admin_disabled",
                "Admin is disabled.",
                503,
                {"enabled": False},
            )
        return None

    def _is_authenticated(config: BenchConfig) -> bool:
        return authenticate_request(config)

    def _check_password(config: BenchConfig):
        if not config.admin.password:
            return error_response(
                "session_unavailable",
                "No admin password is configured.",
                503,
                {"enabled": False},
            )
        if not _is_authenticated(config):
            return error_response(
                "authentication_required",
                "Authentication is required.",
                401,
            )
        from .auth import authorization_error

        view = app.view_functions.get(request.endpoint) if request.endpoint else None
        error = authorization_error(g.jwt_claims, view, request.view_args or {})
        if error:
            return error_response("forbidden", error, 403)
        return None

    @app.before_request
    def _guard():
        g.jwt_claims = None
        if not is_api_path(request.path):
            return None
        view = app.view_functions.get(request.endpoint) if request.endpoint else None
        if view is None:
            return None
        policy = endpoint_auth_policy(view)
        if policy == AuthPolicy.OPEN:
            return None
        is_setup = policy == AuthPolicy.SETUP_CONDITIONAL
        try:
            config = _load_config()
        except Exception:
            if is_setup and not config_store.exists():
                return None
            return error_response(
                "configuration_unavailable",
                "Bench configuration is unavailable.",
                503,
                {"enabled": False},
            )
        if is_setup and not config.admin.password:
            return None
        return _check_enabled(config) or _check_password(config)

    app.register_blueprint(core_bp, url_prefix=API_V1_PREFIX)
    app.register_blueprint(setup_bp, url_prefix=f"{API_V1_PREFIX}/setup")
    app.register_blueprint(dashboard_bp, url_prefix=API_V1_PREFIX)
    app.register_blueprint(apps_bp, url_prefix=f"{API_V1_PREFIX}/apps")
    app.register_blueprint(marketplace_bp, url_prefix=f"{API_V1_PREFIX}/marketplace")
    app.register_blueprint(benches_bp, url_prefix=f"{API_V1_PREFIX}/benches")
    app.register_blueprint(bench_readiness_bp, url_prefix=API_V1_PREFIX)
    app.register_blueprint(sites_bp, url_prefix=f"{API_V1_PREFIX}/sites")
    app.register_blueprint(site_restores_bp, url_prefix=API_V1_PREFIX)
    app.register_blueprint(site_login_bp, url_prefix=API_V1_PREFIX)
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

    install_api_error_handlers(app)

    @app.errorhandler(405)
    def _method_not_allowed(error):
        if is_api_path(request.path):
            return error_response("method_not_allowed", "Method not allowed.", 405)
        return error

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    @allow_unauthenticated
    def serve_spa(path):
        if path == "api" or path.startswith("api/"):
            return error_response("not_found", "API route not found.", 404)
        dist = _STATIC_DIR / "dist"
        if not dist.exists():
            return "Frontend not built. Run: cd admin/frontend && npm install && npm run build", 503
        candidate = dist / path
        if path and candidate.exists() and candidate.is_file():
            return send_file(str(candidate))
        return send_file(str(dist / "index.html"))

    return app


def _trusted_proxy_peers(config_store: BenchTomlStore) -> tuple[str, ...]:
    """Immediate peers allowed to supply nginx's forwarded client headers."""
    try:
        production_enabled = config_store.read().production.enabled
    except Exception:
        production_enabled = False
    if not production_enabled:
        return ()
    # Production nginx reaches the admin over loopback or a Unix socket. An
    # empty REMOTE_ADDR is how the latter is represented by the WSGI server.
    return ("127.0.0.1", "::1", "")


def _secure_cookie_setting(config_store: BenchTomlStore) -> bool:
    """Whether the browser reaches Admin over explicitly configured HTTPS."""
    try:
        config = config_store.read()
    except Exception:
        return False
    if not config.production.enabled:
        return False
    if config.admin.tls:
        return True

    from pilot.core.domains import DomainRouteProvider

    try:
        return bool(DomainRouteProvider.proxy_servers())
    except Exception:
        return False
