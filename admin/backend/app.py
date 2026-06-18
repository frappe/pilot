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

_STATIC_DIR = Path(__file__).parent / "static"
_OPEN_PATHS = {"/api/status", "/api/login", "/api/logout"}
_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
# Lenient hostname: dotted alphanumeric/hyphen labels (allows admin.example.com
# and dev names like my-admin.localhost).
_ADMIN_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)


def _port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


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
                "production": config.production.enabled,
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
        benches_dir = bench_root.parent
        running = []
        for bench_dir in sorted(benches_dir.iterdir()):
            if not bench_dir.is_dir():
                continue
            toml_path = bench_dir / "bench.toml"
            if not toml_path.exists():
                continue
            try:
                with open(toml_path, "rb") as f:
                    config = tomllib.load(f)
                admin = config.get("admin", {})
                prod = config.get("production", {})
                port = admin.get("port")
                name = config.get("bench", {}).get("name", bench_dir.name)
                if not port:
                    continue
                pm = str(prod.get("process_manager", "")).lower()
                pm = "" if pm in ("", "none") else ("supervisor" if pm == "supervisord" else pm)
                production = bool(prod.get("enabled", pm != ""))
                domain = admin.get("domain", "")
                tls = bool(admin.get("tls", False))
                # The admin binds `port` directly in dev, but under socket
                # activation gunicorn binds internal_port (port + 1) and nginx
                # serves the public domain — nothing listens on `port` itself.
                # A production admin stays reachable while its workload is
                # stopped; a stopped dev bench is unavailable (dead port).
                reachable = _port_open(port) or _port_open(port + 1)
                if not reachable:
                    continue
                scheme = "https" if tls else "http"
                admin_url = f"{scheme}://{domain}" if production and domain else ""
                running.append({
                    "name": name,
                    "port": port,
                    "domain": domain,
                    "production": production,
                    "process_manager": pm or None,
                    "runtime_state": "running",
                    "admin_url": admin_url,
                })
            except Exception:
                continue
        return jsonify(running)

    @app.route("/api/benches/new", methods=["POST"])
    def api_benches_new():
        from bench_cli.utils import host_owner, normalize_host

        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name or not _NAME_RE.match(name):
            return jsonify({"error": "Bench name must contain only letters, numbers, '-' and '_'"}), 400

        process_manager = (data.get("process_manager") or "").strip().lower()
        if process_manager == "supervisord":
            process_manager = "supervisor"
        if process_manager not in ("systemd", "supervisor"):
            return jsonify({"error": "Choose a process manager: systemd or supervisor."}), 400

        admin_domain = (data.get("admin_domain") or "").strip()
        if not admin_domain:
            return jsonify({"error": "Admin domain is required so the bench is reachable in production."}), 400
        if not _ADMIN_DOMAIN_RE.match(admin_domain):
            return jsonify({"error": f"'{admin_domain}' is not a valid hostname."}), 400

        new_dir = bench_root.parent / name
        owner = host_owner(new_dir, admin_domain)
        if owner:
            return jsonify({"error": f"Admin domain '{admin_domain}' is already used by bench '{owner}'."}), 400
        if normalize_host(admin_domain) == normalize_host(name):
            return jsonify({"error": "Admin domain must differ from the bench/site name."}), 400

        # TLS termination is a server-wide setting carried forward from sibling
        # benches; only override when the caller explicitly provides it.
        admin_tls = data.get("admin_tls")
        if admin_tls is not None:
            admin_tls = bool(admin_tls)

        try:
            NewCommand(new_dir, name, process_manager=process_manager,
                       admin_domain=admin_domain, admin_tls=admin_tls).run()
        except BenchError as exc:
            return jsonify({"error": str(exc)}), 400

        with open(new_dir / "bench.toml", "rb") as f:
            new_toml = tomllib.load(f)
        new_port = new_toml["admin"]["port"]

        cli_root = _cli_root()
        admin_python = cli_root / ".admin-venv" / "bin" / "python"
        # Strip WERKZEUG_* — if this request is being handled by a dev-mode
        # (--dev) admin server, its env carries WERKZEUG_SERVER_FD/RUN_MAIN
        # from its own reloader. Inheriting those into a child process makes
        # Werkzeug try to reuse a stale fd as an already-bound socket, which
        # crashes it on startup with no visible error.
        spawn_env = {k: v for k, v in os.environ.items() if not k.startswith("WERKZEUG_")}
        spawn_env["PYTHONPATH"] = str(cli_root)

        # A bench created from a production admin should come up the same way:
        # provision it to production (init + setup production) in the background
        # and let the dialog redirect to its own domain. A dev admin keeps the
        # raw-port wizard flow.
        if _current_is_production():
            subprocess.Popen(
                [str(admin_python), "-m", "admin.backend.provision_bench", str(new_dir)],
                cwd=str(cli_root), env=spawn_env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
            )
            admin = new_toml.get("admin", {})
            scheme = "https" if admin.get("tls", False) else "http"
            return jsonify({
                "name": name, "port": new_port, "production": True,
                "domain": admin.get("domain", ""), "scheme": scheme,
            })

        subprocess.Popen(
            [str(admin_python), "-m", "admin.backend.server",
             "--bench-root", str(new_dir), "--port", str(new_port),
             "--timeout", "7200", "--wizard"],
            cwd=str(cli_root), env=spawn_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        return jsonify({"name": name, "port": new_port, "production": False})

    def _current_is_production() -> bool:
        # Read the flag straight from toml (no full validation) so a slightly
        # incomplete current config can't block creating a new bench.
        try:
            with open(bench_root / "bench.toml", "rb") as f:
                prod = tomllib.load(f).get("production", {})
            pm = str(prod.get("process_manager", "")).lower()
            return bool(prod.get("enabled", pm not in ("", "none")))
        except Exception:
            return False

    @app.route("/api/benches/provision-status")
    def api_benches_provision_status():
        name = (request.args.get("name") or "").strip()
        if not name or not _NAME_RE.match(name):
            return jsonify({"error": "Invalid bench name"}), 400
        new_dir = bench_root.parent / name
        status_file = new_dir / "provision.status"
        log_file = new_dir / "provision.log"
        status = status_file.read_text().strip() if status_file.exists() else "unknown"
        log_tail = ""
        if log_file.exists():
            # Tail the log so the dialog can show recent progress without
            # streaming the whole file every poll.
            log_tail = "\n".join(log_file.read_text().splitlines()[-40:])
        return jsonify({"status": status, "log": log_tail})

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
