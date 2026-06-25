from __future__ import annotations

import http.client
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
from .views.git import git_bp
from .views.stats import stats_bp
from .views.database import database_bp
from .views.logs import logs_bp
from .views.processes import processes_bp
from .views.setup import setup_bp, wizard_marker_path
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
_OPEN_PATHS = {"/api/status", "/api/login", "/api/logout", "/api/ping"}
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


def _workload_running(bench_dir: Path, toml_path: Path) -> bool | None:
    """Whether a production bench's workload is currently running — used to
    gate start/stop/restart controls for other benches in the switcher. None
    if the check itself fails (e.g. process manager CLI not installed)."""
    from bench_cli.config.bench_config import BenchConfig
    from bench_cli.core.bench import Bench
    from bench_cli.managers.process_manager import ProcessManagerFactory

    try:
        bench = Bench(BenchConfig.from_file(toml_path), bench_dir)
        return ProcessManagerFactory.create(bench).is_running()
    except Exception:
        return None


def _admin_running(bench_dir: Path, toml_path: Path) -> bool | None:
    """Whether a production bench's admin control plane is up — socket-activated,
    so a listening socket counts even while the workload is stopped. Lets the UI
    show 'Admin active' instead of 'Stopped' for a provisioned-but-not-started
    bench. None if the check fails."""
    from bench_cli.config.bench_config import BenchConfig
    from bench_cli.core.bench import Bench
    from bench_cli.managers.process_manager import ProcessManagerFactory

    try:
        bench = Bench(BenchConfig.from_file(toml_path), bench_dir)
        return ProcessManagerFactory.create(bench).admin_is_running()
    except Exception:
        return None


def _admin_cert_exists(bench_dir: Path, toml_path: Path) -> bool:
    """Whether the admin domain's TLS cert is in place — gates whether nginx
    serves the admin over https yet. False on any failure (treat as plain http)."""
    from bench_cli.config.bench_config import BenchConfig
    from bench_cli.core.bench import Bench
    from bench_cli.managers.nginx_manager import NginxManager

    try:
        bench = Bench(BenchConfig.from_file(toml_path), bench_dir)
        return NginxManager(bench).admin_cert_exists()
    except Exception:
        return False


def _site_count(bench_dir: Path) -> int:
    """Number of real sites in a bench — a sites/ subdir is a site iff it holds a
    site_config.json (skips assets/, apps.txt, etc.)."""
    sites_dir = bench_dir / "sites"
    if not sites_dir.is_dir():
        return 0
    return sum(1 for d in sites_dir.iterdir() if d.is_dir() and (d / "site_config.json").exists())


def _persist_toml(bench_dir: Path, updates: dict) -> None:
    """Merge ``updates`` into a bench's bench.toml in place, preserving other keys."""
    from bench_cli.utils import write_toml

    toml_path = bench_dir / "bench.toml"
    data = tomllib.loads(toml_path.read_text())
    for section, values in updates.items():
        data.setdefault(section, {}).update(values)
    write_toml(toml_path, data)


def _wizard_status(bench_root: Path) -> dict:
    name = bench_root.name
    try:
        with open(bench_root / "bench.toml", "rb") as f:
            name = tomllib.load(f).get("bench", {}).get("name", name)
    except Exception:
        pass
    return {"wizard": True, "name": name, "enabled": True, "authenticated": True}


def _setup_complete(bench_root: Path, config: BenchConfig) -> bool:
    """Whether first-time setup has fully finished — used to retire a stale wizard
    marker. A production bench isn't done until production is enabled (the deploy
    sets it last); a dev bench is done once init has. Either way, the wizard's
    setup task must no longer be running."""
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

    def _is_authenticated(config: BenchConfig) -> bool:
        if session.get("authenticated"):
            return True
        from bench_cli.commands.generate_session import verify_token

        return verify_token(request.cookies.get("sid", ""), config.admin.jwt_secret)

    def _check_password(config: BenchConfig):
        if not config.admin.password:
            return jsonify({"error": "No admin password configured in bench.toml", "enabled": False}), 503
        if not _is_authenticated(config):
            return jsonify({"error": "Authentication required"}), 401
        return None

    @app.before_request
    def _guard():
        if not request.path.startswith("/api") or request.path in _OPEN_PATHS:
            return None
        is_setup = request.path.startswith("/api/setup/")
        try:
            config = _load_config()
        except Exception as exc:
            return None if is_setup else (jsonify({"error": str(exc), "enabled": False}), 503)
        if is_setup and not config.admin.password:
            return None
        return _check_enabled(config) or _check_password(config)

    @app.route("/api/ping")
    def api_ping():
        # Liveness check: no auth, always 200. CORS-open so the frontend can probe
        # both http:// and https:// of the current host while recovering.
        resp = jsonify({"ok": True})
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    @app.route("/api/status")
    def api_status():
        initialized = (bench_root / "env" / "bin" / "python").exists()
        try:
            config = BenchConfig.from_file(bench_root / "bench.toml")
        except Exception as exc:
            return jsonify({"enabled": False, "error": str(exc)}), 503
        if not initialized or not config.admin.password:
            return jsonify(_wizard_status(bench_root))
        # The bench looks initialized, but the wizard's own production deploy may
        # still be mid-flight (env + password exist before production is enabled).
        # The wizard marker tells that apart from an independent `setup production`
        # re-run, which never writes it. Clear a stale marker once setup is truly
        # complete so a crashed/closed wizard can't trap the bench in setup mode.
        marker = wizard_marker_path(bench_root)
        if marker.exists():
            if _setup_complete(bench_root, config):
                marker.unlink(missing_ok=True)
            else:
                return jsonify(_wizard_status(bench_root))
        from bench_cli.platform import native_process_manager

        return jsonify(
            {
                "enabled": config.admin.enabled,
                "name": config.name,
                "production": config.production.enabled,
                "native_process_manager": native_process_manager(),
                "authenticated": _is_authenticated(config),
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
        benches = []
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
                # Open over the scheme nginx actually serves: https only once the
                # cert is in place, else http — a provisioned-but-not-set-up bench
                # is served plain http even when tls is configured.
                serves_https = tls and _admin_cert_exists(bench_dir, toml_path)
                scheme = "https" if serves_https else "http"
                admin_url = f"{scheme}://{domain}" if production and domain else ""
                workload_running = _workload_running(bench_dir, toml_path) if production else None
                admin_running = _admin_running(bench_dir, toml_path) if production else None
                benches.append({
                    "name": name,
                    "port": port,
                    "domain": domain,
                    "production": production,
                    "process_manager": pm or None,
                    "reachable": reachable,
                    "admin_url": admin_url,
                    "workload_running": workload_running,
                    "admin_running": admin_running,
                    "site_count": _site_count(bench_dir),
                })
            except Exception:
                continue
        return jsonify(benches)

    @app.route("/api/benches/<name>/<action>", methods=["POST"])
    def api_benches_control(name, action):
        if action not in ("start", "stop", "restart"):
            return jsonify({"ok": False, "error": f"Unknown action '{action}'."}), 400
        if not _NAME_RE.match(name):
            return jsonify({"ok": False, "error": "Invalid bench name."}), 400

        target_dir = bench_root.parent / name
        toml_path = target_dir / "bench.toml"
        if not toml_path.exists():
            return jsonify({"ok": False, "error": f"Bench '{name}' not found."}), 404

        try:
            with open(toml_path, "rb") as f:
                target_config = tomllib.load(f)
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        if not target_config.get("production", {}).get("enabled"):
            return jsonify({"ok": False, "error": "Start/stop/restart from here is only supported for production benches."}), 400

        cli_root = _cli_root()
        try:
            result = subprocess.run(
                [str(cli_root / "bench"), "-b", name, action],
                cwd=cli_root, capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            return jsonify({"ok": False, "error": f"'{action}' timed out."}), 500
        if result.returncode != 0:
            return jsonify({"ok": False, "error": (result.stderr or result.stdout).strip()}), 500
        return jsonify({"ok": True})

    @app.route("/api/benches/<name>", methods=["DELETE"])
    def api_benches_drop(name):
        if not _NAME_RE.match(name):
            return jsonify({"ok": False, "error": "Invalid bench name."}), 400

        target_dir = bench_root.parent / name
        toml_path = target_dir / "bench.toml"
        if not toml_path.exists():
            return jsonify({"ok": False, "error": f"Bench '{name}' not found."}), 404
        if target_dir.resolve() == bench_root.resolve():
            return jsonify({"ok": False, "error": "Can't drop the bench you're currently using."}), 400

        # The drop itself re-checks for sites, but reject early with a clear
        # message rather than shelling out only to fail.
        sites = _site_count(target_dir)
        if sites:
            return jsonify({"ok": False, "error": f"Bench '{name}' has {sites} site(s). Drop them first."}), 400

        cli_root = _cli_root()
        try:
            result = subprocess.run(
                [str(cli_root / "bench"), "--yes", "-b", name, "drop"],
                cwd=cli_root, capture_output=True, text=True, timeout=180,
            )
        except subprocess.TimeoutExpired:
            return jsonify({"ok": False, "error": "Drop timed out."}), 500
        if result.returncode != 0:
            return jsonify({"ok": False, "error": (result.stderr or result.stdout).strip()}), 500
        return jsonify({"ok": True})

    @app.route("/api/benches/wildcard-domains", methods=["GET"])
    def api_benches_wildcard_domains():
        """Wildcard domain suffixes (no leading '*') new bench admin domains may be built from."""
        from bench_cli.core.domain_controller import DomainRouteProvider
        from bench_cli.utils import wildcard_suffix

        try:
            patterns = DomainRouteProvider.wildcard_domains()
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"domains": [wildcard_suffix(p) for p in patterns]})

    @app.route("/api/benches/new", methods=["POST"])
    def api_benches_new():
        from bench_cli.utils import host_owner, normalize_host

        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        if not name or not _NAME_RE.match(name):
            return jsonify({"error": "Bench name must contain only letters, numbers, '-' and '_'"}), 400

        from bench_cli.config.production_config import VALID_PROCESS_MANAGERS
        from bench_cli.platform import is_alpine

        process_manager = (data.get("process_manager") or "").strip().lower()
        if process_manager == "supervisord":
            process_manager = "supervisor"
        if process_manager not in VALID_PROCESS_MANAGERS:
            return jsonify({"error": f"Choose a process manager: {', '.join(VALID_PROCESS_MANAGERS)}."}), 400
        if is_alpine() and process_manager == "systemd":
            # Alpine has no systemd; the UI offers OpenRC there, but coerce any
            # stale systemd request to OpenRC (the native Alpine manager) so a
            # cached client can never deploy an unmanageable bench.
            process_manager = "openrc"

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

        from bench_cli.core.domain_controller import DomainRouteProvider
        from bench_cli.utils import matches_wildcard

        patterns = DomainRouteProvider.wildcard_domains()
        if patterns and not matches_wildcard(admin_domain, patterns):
            return jsonify({"error": f"Admin domain must match one of: {', '.join(patterns)}."}), 400

        # New benches from the UI come up plain HTTP; the user enables HTTPS
        # later from Settings (or the wizard). Never inherit a sibling's TLS here.
        admin_tls = bool(data.get("admin_tls", False))

        try:
            NewCommand(new_dir, name, process_manager=process_manager,
                       admin_domain=admin_domain, admin_tls=admin_tls).run()
        except BenchError as exc:
            return jsonify({"error": str(exc)}), 400

        with open(new_dir / "bench.toml", "rb") as f:
            new_toml = tomllib.load(f)
        new_port = new_toml["admin"]["port"]

        cli_root = _cli_root()
        admin = new_toml.get("admin", {})

        # A production parent brings up the new bench's OWN admin service (socket-
        # activated, self-managing) and routes its domain to it. The admin serves
        # the setup wizard until the bench is initialized; the user then runs
        # `setup production` from it, which starts the workload. No standalone
        # wizard server — the bench's admin handles its own lifecycle.
        if _current_is_production():
            try:
                from bench_cli.config.bench_config import BenchConfig
                from bench_cli.core.bench import Bench
                from bench_cli.managers.nginx_manager import NginxManager

                bench = Bench(BenchConfig.from_file(new_dir / "bench.toml"), new_dir)
                # Register the admin domain with the domain provider (if any) before
                # routing it, so it resolves to this server — the wizard is reached
                # at this domain. The wizard's later `setup production` sees the
                # domain unchanged and won't re-register it.
                DomainRouteProvider(bench).register(admin_domain, admin_domain)
                # Not deployed yet (production.enabled is false at this point), so
                # pick the manager by the configured process_manager rather than
                # via the factory, which gates on enabled.
                configured_pm = bench.config.production.process_manager
                if configured_pm == "systemd":
                    from bench_cli.managers.systemd_process_manager import SystemdProcessManager as PM
                elif configured_pm == "openrc":
                    from bench_cli.managers.openrc_process_manager import OpenRCProcessManager as PM
                else:
                    from bench_cli.managers.supervisor_process_manager import SupervisorProcessManager as PM
                PM(bench).setup_admin()
                nginx = NginxManager(bench)
                nginx.generate_config()
                nginx.install_config()
                nginx.reload()
                # The admin now runs under the chosen process manager, so record
                # the bench as production — otherwise `bench status`/`stop` fall
                # back to the foreground (Procfile) manager and misreport it. The
                # workload simply stays stopped until the user finishes setup.
                _persist_toml(new_dir, {"production": {"enabled": True}})
                # The wizard is reached over the scheme nginx serves *now* — https
                # only once the cert is actually in place, else http (TLS is set up
                # later). Report it so the client never redirects to a scheme that
                # isn't listening yet.
                serves_https = bool(bench.config.admin.tls and nginx.admin_cert_exists())
                # The IP the domain's A record should point at, so the browser can
                # confirm (via DoH) that DNS has propagated to it specifically.
                server_ip = DomainRouteProvider._server_ip()
            except Exception as exc:
                return jsonify({"error": f"Failed to bring up the new bench: {exc}"}), 500
            return jsonify({"name": name, "port": new_port, "wizard_at_domain": True,
                            "domain": admin.get("domain", ""),
                            "scheme": "https" if serves_https else "http",
                            "server_ip": server_ip})

        # Dev parent (no process manager): run a standalone wizard server on the
        # bench's admin port and reach it on this host. Strip WERKZEUG_* so a
        # dev-mode reloader's stale socket fd isn't inherited; strip BENCH_ADMIN_*
        # so the admin's idle-timeout/root don't leak in.
        spawn_env = {
            k: v for k, v in os.environ.items()
            if not k.startswith("WERKZEUG_") and not k.startswith("BENCH_ADMIN_")
        }
        spawn_env["PYTHONPATH"] = str(cli_root)
        subprocess.Popen(
            [str(cli_root / ".admin-venv" / "bin" / "python"), "-m", "admin.backend.server",
             "--bench-root", str(new_dir), "--port", str(new_port), "--timeout", "7200", "--wizard"],
            cwd=str(cli_root), env=spawn_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        return jsonify({"name": name, "port": new_port, "wizard_at_domain": False,
                        "domain": admin.get("domain", "")})

    def _nginx_http_port() -> int:
        # Shared host: one nginx serves every bench on the same http_port.
        try:
            with open(bench_root / "bench.toml", "rb") as f:
                return int(tomllib.load(f).get("nginx", {}).get("http_port", 80))
        except Exception:
            return 80

    def _wizard_responds(domain: str, scheme: str = "http") -> bool:
        """Readiness for a production bench's wizard: nginx routes the domain to the
        admin and the admin answers. Probed over loopback with a Host header, so no
        DNS (and no stale DNS cache) is involved — DNS propagation to the browser is
        the client's concern. /api/ping is unauthenticated and always 200 over http;
        for an https bench :80 instead redirects to https, and that redirect only
        exists once the cert is in place — so it is itself the readiness signal."""
        conn = http.client.HTTPConnection("127.0.0.1", _nginx_http_port(), timeout=3)
        try:
            conn.request("GET", "/api/ping", headers={"Host": domain})
            status = conn.getresponse().status
            return status == 200 or (scheme == "https" and status in (301, 308))
        except OSError:
            return False
        finally:
            conn.close()

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

    @app.route("/api/benches/ready")
    def api_benches_ready():
        # A production bench is reached at its admin domain: ready once the wizard
        # actually answers there (DNS propagated, nginx routing, admin up).
        domain = (request.args.get("domain") or "").strip()
        if domain:
            scheme = (request.args.get("scheme") or "http").strip()
            return jsonify({"ready": _wizard_responds(domain, scheme)})
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
    app.register_blueprint(git_bp, url_prefix="/api/git")
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
