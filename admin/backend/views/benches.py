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
from pilot.loader import cli_root
from pilot.commands.new import NewCommand
from pilot.config.toml_store import BenchTomlStore
from pilot.exceptions import BenchAlreadyExistsError, BenchError

from ..api_contract import error_response

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
        return error_response(
            "bench_management_forbidden", "Bench management is disabled on this server.", 403
        )
    if not config.get("admin", {}).get("allow_bench_management", True):
        return error_response(
            "bench_management_forbidden", "Bench management is disabled on this server.", 403
        )


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
        return error_response("invalid_bench_action", f"Unknown action '{action_name}'.", 422)
    if not _NAME_RE.match(name):
        return error_response("invalid_bench_name", "Invalid bench name.", 422)

    target_dir = bench_root.parent / name
    toml_path = target_dir / "bench.toml"
    if not toml_path.exists():
        return error_response("bench_not_found", f"Bench '{name}' not found.", 404)

    try:
        target_config = BenchTomlStore(toml_path).read_raw()
    except Exception:
        return error_response("bench_unavailable", "Could not read the bench configuration.", 500)
    if not target_config.get("production", {}).get("enabled"):
        return error_response(
            "bench_action_unavailable",
            "Start, stop, and restart are only supported for production benches.",
            409,
        )

    root = cli_root()
    try:
        result = subprocess.run(
            [str(root / "bench"), "-b", name, action_name],
            cwd=root, capture_output=True, text=True, timeout=60,
        )
    except subprocess.TimeoutExpired:
        return error_response("bench_action_failed", "The bench action timed out.", 500)
    if result.returncode != 0:
        return error_response("bench_action_failed", "Could not complete the bench action.", 500)
    return jsonify({"ok": True})


@benches_bp.route("/<name>", methods=["DELETE"])
def drop(name):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not _NAME_RE.match(name):
        return error_response("invalid_bench_name", "Invalid bench name.", 422)

    target_dir = bench_root.parent / name
    toml_path = target_dir / "bench.toml"
    if not toml_path.exists():
        return error_response("bench_not_found", f"Bench '{name}' not found.", 404)
    if target_dir.resolve() == bench_root.resolve():
        return error_response("bench_drop_conflict", "The active bench cannot be dropped.", 409)

    sites = site_count(target_dir)
    if sites:
        return error_response(
            "bench_not_empty",
            f"Bench '{name}' has {sites} site(s). Drop them first.",
            409,
        )

    root = cli_root()
    try:
        result = subprocess.run(
            [str(root / "bench"), "--yes", "-b", name, "drop"],
            cwd=root, capture_output=True, text=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        return error_response("bench_drop_failed", "Dropping the bench timed out.", 500)
    if result.returncode != 0:
        return error_response("bench_drop_failed", "Could not drop the bench.", 500)
    return jsonify({"ok": True})


@benches_bp.route("/wildcard-domains", methods=["GET"])
def wildcard_domains():
    """Wildcard domain suffixes new bench admin domains may be built from."""
    from pilot.core.domain_controller import DomainRouteProvider
    from pilot.utils import wildcard_suffix

    try:
        patterns = DomainRouteProvider.wildcard_domains()
    except Exception:
        return error_response(
            "wildcard_domains_unavailable", "Could not read wildcard domains.", 500
        )
    return jsonify({"domains": [wildcard_suffix(p) for p in patterns]})


@benches_bp.route("/new", methods=["POST"])
def new():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    from pilot.utils import host_owner, normalize_host

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    if any(
        value is not None and not isinstance(value, str)
        for value in (
            data.get("name"),
            data.get("process_manager"),
            data.get("db_type"),
            data.get("admin_domain"),
        )
    ):
        return error_response("invalid_bench", "Bench fields must be strings.", 422)
    if "admin_tls" in data and not isinstance(data["admin_tls"], bool):
        return error_response("invalid_admin_tls", "admin_tls must be a boolean.", 422)

    name = (data.get("name") or "").strip()
    if not name or not _NAME_RE.match(name):
        return error_response(
            "invalid_bench_name",
            "Bench name must contain only letters, numbers, '-' and '_'.",
            422,
        )

    from pilot.config.production_config import VALID_PROCESS_MANAGERS

    process_manager = (data.get("process_manager") or "").strip().lower()
    if process_manager == "supervisord":
        process_manager = "supervisor"
    if process_manager not in VALID_PROCESS_MANAGERS:
        return error_response(
            "invalid_process_manager",
            f"Choose a process manager: {', '.join(VALID_PROCESS_MANAGERS)}.",
            422,
        )

    db_type = (data.get("db_type") or "mariadb").strip()
    if db_type not in ("mariadb", "postgres", "sqlite"):
        return error_response(
            "invalid_database",
            "Database must be 'mariadb', 'postgres', or 'sqlite'.",
            422,
        )

    admin_domain = (data.get("admin_domain") or "").strip()
    if not admin_domain:
        return error_response(
            "admin_domain_required",
            "Admin domain is required so the bench is reachable in production.",
            422,
        )
    if not _ADMIN_DOMAIN_RE.match(admin_domain):
        return error_response(
            "invalid_admin_domain", f"'{admin_domain}' is not a valid hostname.", 422
        )

    new_dir = bench_root.parent / name
    owner = host_owner(new_dir, admin_domain)
    if owner:
        return error_response(
            "admin_domain_conflict",
            f"Admin domain '{admin_domain}' is already used by bench '{owner}'.",
            409,
        )
    if normalize_host(admin_domain) == normalize_host(name):
        return error_response(
            "invalid_admin_domain",
            "Admin domain must differ from the bench/site name.",
            422,
        )

    from pilot.core.domain_controller import DomainRouteProvider
    from pilot.utils import matches_wildcard

    patterns = DomainRouteProvider.wildcard_domains()
    if patterns and not matches_wildcard(admin_domain, patterns):
        return error_response(
            "invalid_admin_domain",
            f"Admin domain must match one of: {', '.join(patterns)}.",
            422,
        )

    # None (client never sends this) lets NewCommand inherit the sibling
    # production bench's TLS choice instead of forcing HTTP-only.
    admin_tls = bool(data["admin_tls"]) if "admin_tls" in data else None

    try:
        NewCommand(
            new_dir,
            name,
            process_manager=process_manager,
            admin_domain=admin_domain,
            admin_tls=admin_tls,
            db_type=db_type,
        ).run()
    except BenchAlreadyExistsError:
        return error_response(
            "bench_already_exists",
            f"Bench '{name}' already exists.",
            409,
        )
    except BenchError as exc:
        return error_response("invalid_bench", str(exc), 422)

    new_toml = BenchTomlStore.for_bench(new_dir).read_raw()
    new_port = new_toml["admin"]["port"]

    root = cli_root()
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
        except Exception:
            return error_response("bench_start_failed", "Could not start the new bench.", 500)
        return jsonify({"name": name, "port": new_port, "wizard_at_domain": True,
                        "domain": admin_cfg.get("domain", ""),
                        "scheme": "http",
                        "server_ip": server_ip})

    spawn_env = {
        k: v for k, v in os.environ.items()
        if not k.startswith("WERKZEUG_") and not k.startswith("BENCH_ADMIN_")
    }
    spawn_env["PYTHONPATH"] = str(root)
    try:
        subprocess.Popen(
            [
                str(root / ".admin-venv" / "bin" / "python"),
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
            cwd=str(root), env=spawn_env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError:
        return error_response("bench_start_failed", "Could not start the new bench.", 500)
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
        return error_response("invalid_port", "port must be an integer.", 422)
    if not 1 <= port <= 65535:
        return error_response("invalid_port", "port must be between 1 and 65535.", 422)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            pass
        return jsonify({"ready": True})
    except OSError:
        return jsonify({"ready": False})
