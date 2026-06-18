from __future__ import annotations

import os
import re
import secrets
import socket
import subprocess
import tomllib
import signal
import threading
import time
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
from bench_cli.commands.admin import _cli_root
from bench_cli.commands.new import NewCommand
from bench_cli.config.bench_config import BenchConfig
from bench_cli.exceptions import BenchError, ConfigError
from bench_cli.utils import iter_sibling_benches

_STATIC_DIR = Path(__file__).parent / "static"
_OPEN_PATHS = {"/api/status", "/api/login", "/api/logout"}
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


def _wizard_status(bench_root: Path) -> dict:
    name = bench_root.name
    try:
        with open(bench_root / "bench.toml", "rb") as f:
            name = tomllib.load(f).get("bench", {}).get("name", name)
    except Exception:
        pass
    return {"wizard": True, "name": name, "enabled": True, "authenticated": True}


def _install_idle_watchdog(app: Flask) -> None:
    """Stop the admin after a period of inactivity when socket-activated.

    Enabled only when BENCH_ADMIN_IDLE_TIMEOUT is set, which the systemd service
    unit does. Under gunicorn (workers=1, preload_app=False) this runs in the
    worker, so os.getppid() is the gunicorn arbiter — SIGTERM to it triggers a
    graceful shutdown and the service stops. systemd keeps the .socket listening,
    so the next request re-activates the service.
    """
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
    app.secret_key = secrets.token_hex(32)
    app.config["SESSION_COOKIE_NAME"] = f"bench_session_{bench_root.name}"

    _install_idle_watchdog(app)

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

    @app.route("/api/benches/")
    def api_benches():
        def _is_running(port: int) -> bool:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    pass
                return True
            except OSError:
                return False

        def _entry(bench_dir: Path, config: BenchConfig) -> dict | None:
            port = config.admin.port
            if not port:
                return None
            return {
                "name": config.name or bench_dir.name,
                "port": port,
                "running": _is_running(port),
            }

        benches = []
        try:
            current = BenchConfig.from_file(bench_root / "bench.toml")
            entry = _entry(bench_root, current)
            if entry:
                benches.append(entry)
        except Exception:
            pass
        for bench_dir, config in iter_sibling_benches(bench_root):
            entry = _entry(bench_dir, config)
            if entry:
                benches.append(entry)
        benches.sort(key=lambda b: b["name"])
        return jsonify(benches)

    @app.route("/api/benches/new", methods=["POST"])
    def api_benches_new():
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name or not _NAME_RE.match(name):
            return jsonify({"error": "Bench name must contain only letters, numbers, '-' and '_'"}), 400

        new_dir = bench_root.parent / name
        try:
            NewCommand(new_dir, name).run()
        except BenchError as exc:
            return jsonify({"error": str(exc)}), 400

        with open(new_dir / "bench.toml", "rb") as f:
            new_port = tomllib.load(f)["admin"]["port"]

        cli_root = _cli_root()
        admin_python = cli_root / ".admin-venv" / "bin" / "python"
        # Strip WERKZEUG_* — if this request is being handled by a dev-mode
        # (--dev) admin server, its env carries WERKZEUG_SERVER_FD/RUN_MAIN
        # from its own reloader. Inheriting those into the new bench's admin
        # process makes Werkzeug try to reuse a stale fd as an already-bound
        # socket, which crashes it on startup with no visible error.
        # Since this is just spawining the setup server we can ignore the phantom
        # process runner it will be killed once the setup is completed anyways.
        spawn_env = {k: v for k, v in os.environ.items() if not k.startswith("WERKZEUG_")}
        spawn_env["PYTHONPATH"] = str(cli_root)
        subprocess.Popen(
            [
                str(admin_python),
                "-m",
                "admin.backend.server",
                "--bench-root",
                str(new_dir),
                "--port",
                str(new_port),
                "--timeout",
                "7200",
                "--wizard",
            ],
            cwd=str(cli_root),
            env=spawn_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return jsonify({"name": name, "port": new_port})

    @app.route("/api/benches/ready")
    def api_benches_ready():
        try:
            port = int(request.args.get("port", ""))
        except ValueError:
            return jsonify({"ready": False}), 400
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                pass
            return jsonify({"ready": True})
        except OSError:
            return jsonify({"ready": False})

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
