from __future__ import annotations

import os
import re
import socket
import subprocess
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from admin.backend.bench_helpers import (
    admin_cert_exists,
    admin_running,
    current_is_production,
    port_open,
    site_count,
    wizard_responds,
    workload_running,
)
from pilot.commands.admin import _cli_root
from pilot.commands.new import NewCommand
from pilot.config.toml_store import BenchTomlStore
from pilot.exceptions import BenchError

benches_bp = Blueprint("benches", __name__)

_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_ADMIN_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)

@benches_bp.before_request
def guard_bench_management():
    """The multi-bench UI and its API are gated by admin.allow_bench_management.
    When off, every route here 403s — the CLI is the way to manage benches then."""
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        config = BenchTomlStore.for_bench(bench_root).read_raw()
    except Exception:
        return None
    if not config.get("admin", {}).get("allow_bench_management", True):
        return jsonify({"error": "Bench management is disabled on this server."}), 403




@benches_bp.route("/")
def get_all():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    benches_dir = bench_root.parent
    benches = []
    for bench_dir in sorted(benches_dir.iterdir()):
        if not bench_dir.is_dir():
            continue
        toml_path = bench_dir / "bench.toml"
        if not toml_path.exists():
            continue
        try:
            config = BenchTomlStore(toml_path).read_raw()
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
            reachable = port_open(port) or port_open(port + 1)
            serves_https = tls and admin_cert_exists(bench_dir, toml_path)
            scheme = "https" if serves_https else "http"
            admin_url = f"{scheme}://{domain}" if production and domain else ""
            workload = workload_running(bench_dir, toml_path) if production else None
            admin = admin_running(bench_dir, toml_path) if production else None
            benches.append({
                "name": name,
                "port": port,
                "domain": domain,
                "production": production,
                "process_manager": pm or None,
                "reachable": reachable,
                "admin_url": admin_url,
                "workload_running": workload,
                "admin_running": admin,
                "site_count": site_count(bench_dir),
            })
        except Exception:
            continue
    return jsonify(benches)


@benches_bp.route("/<name>/actions/<action_name>", methods=["POST"])
def run_action(name, action_name):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if action_name not in ("start", "stop", "restart"):
        return jsonify({"ok": False, "error": f"Unknown action '{action_name}'."}), 400
    if not _NAME_RE.match(name):
        return jsonify({"ok": False, "error": "Invalid bench name."}), 400

    target_dir = bench_root.parent / name
    toml_path = target_dir / "bench.toml"
    if not toml_path.exists():
        return jsonify({"ok": False, "error": f"Bench '{name}' not found."}), 404

    try:
        target_config = BenchTomlStore(toml_path).read_raw()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    if not target_config.get("production", {}).get("enabled"):
        return jsonify({"ok": False, "error": "Start/stop/restart from here is only supported for production benches."}), 400

    cli_root = _cli_root()
    try:
        result = subprocess.run(
            [str(cli_root / "bench"), "-b", name, action_name],
            cwd=cli_root, capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": f"'{action_name}' timed out."}), 500
    if result.returncode != 0:
        return jsonify({"ok": False, "error": (result.stderr or result.stdout).strip()}), 500
    return jsonify({"ok": True})


@benches_bp.route("/<name>", methods=["DELETE"])
def drop(name):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not _NAME_RE.match(name):
        return jsonify({"ok": False, "error": "Invalid bench name."}), 400

    target_dir = bench_root.parent / name
    toml_path = target_dir / "bench.toml"
    if not toml_path.exists():
        return jsonify({"ok": False, "error": f"Bench '{name}' not found."}), 404
    if target_dir.resolve() == bench_root.resolve():
        return jsonify({"ok": False, "error": "Can't drop the bench you're currently using."}), 400

    sites = site_count(target_dir)
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


@benches_bp.route("/wildcard-domains", methods=["GET"])
def wildcard_domains():
    """Wildcard domain suffixes new bench admin domains may be built from."""
    from pilot.core.domain_controller import DomainRouteProvider
    from pilot.utils import wildcard_suffix

    try:
        patterns = DomainRouteProvider.wildcard_domains()
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"domains": [wildcard_suffix(p) for p in patterns]})


@benches_bp.route("/new", methods=["POST"])
def new():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    from pilot.utils import host_owner, normalize_host

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name or not _NAME_RE.match(name):
        return jsonify({"error": "Bench name must contain only letters, numbers, '-' and '_'"}), 400

    from pilot.config.production_config import VALID_PROCESS_MANAGERS
    from pilot.platform import is_alpine

    process_manager = (data.get("process_manager") or "").strip().lower()
    if process_manager == "supervisord":
        process_manager = "supervisor"
    if process_manager not in VALID_PROCESS_MANAGERS:
        return jsonify({"error": f"Choose a process manager: {', '.join(VALID_PROCESS_MANAGERS)}."}), 400
    if is_alpine() and process_manager == "systemd":
        process_manager = "openrc"

    db_type = (data.get("db_type") or "mariadb").strip()
    if db_type not in ("mariadb", "postgres", "sqlite"):
        return jsonify({"error": "Database must be 'mariadb', 'postgres', or 'sqlite'."}), 400

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

    from pilot.core.domain_controller import DomainRouteProvider
    from pilot.utils import matches_wildcard

    patterns = DomainRouteProvider.wildcard_domains()
    if patterns and not matches_wildcard(admin_domain, patterns):
        return jsonify({"error": f"Admin domain must match one of: {', '.join(patterns)}."}), 400

    # None (client never sends this) lets NewCommand inherit the sibling
    # production bench's TLS choice instead of forcing HTTP-only.
    admin_tls = bool(data["admin_tls"]) if "admin_tls" in data else None

    try:
        NewCommand(new_dir, name, process_manager=process_manager,
                   admin_domain=admin_domain, admin_tls=admin_tls, db_type=db_type).run()
    except BenchError as exc:
        return jsonify({"error": str(exc)}), 400

    new_toml = BenchTomlStore.for_bench(new_dir).read_raw()
    new_port = new_toml["admin"]["port"]

    cli_root = _cli_root()
    admin_cfg = new_toml.get("admin", {})

    if current_is_production(bench_root):
        try:
            from pilot.core.bench import Bench
            from pilot.managers.nginx_manager import NginxManager

            bench = Bench(BenchTomlStore.for_bench(new_dir).read(), new_dir)
            DomainRouteProvider(bench).register(admin_domain, admin_domain)
            configured_pm = bench.config.production.process_manager
            if configured_pm == "systemd":
                from pilot.managers.process_managers.systemd import SystemdProcessManager as PM
            elif configured_pm == "openrc":
                from pilot.managers.process_managers.openrc import OpenRCProcessManager as PM
            else:
                from pilot.managers.process_managers.supervisor import SupervisorProcessManager as PM
            pm = PM(bench)
            pm.start_admin()
            # Just enough to make the wizard reachable at its domain (over plain
            # HTTP). The workload, TLS, and marking production.enabled all need
            # the venv/framework app the wizard's init step installs, so
            # WizardSetupTask finishes the rest via SetupProductionCommand once
            # that's done - duplicating those steps here risks running them
            # before the bench can actually support them (see git history).
            nginx = NginxManager(bench)
            nginx.generate_config()
            nginx.install_config()
            server_ip = DomainRouteProvider._server_ip()
        except Exception as exc:
            return jsonify({"error": f"Failed to bring up the new bench: {exc}"}), 500
        return jsonify({"name": name, "port": new_port, "wizard_at_domain": True,
                        "domain": admin_cfg.get("domain", ""),
                        "scheme": "http",
                        "server_ip": server_ip})

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
                    "domain": admin_cfg.get("domain", "")})


@benches_bp.route("/ready")
def is_ready():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    domain = (request.args.get("domain") or "").strip()
    if domain:
        scheme = (request.args.get("scheme") or "http").strip()
        return jsonify({"ready": wizard_responds(bench_root, domain, scheme)})
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
