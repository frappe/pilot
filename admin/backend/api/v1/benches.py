from __future__ import annotations

import os
import re
import socket
import subprocess
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, url_for

from admin.backend.providers.bench import BenchProvider
from pilot.config.toml_store import BenchTomlStore
from pilot.core.bench import Bench
from pilot.exceptions import BenchAlreadyExistsError, BenchError
from pilot.internal.atomic_file import exclusive_file_lock
from pilot.loader import cli_root
from pilot.managers.processes.local import ProcessManager

from admin.backend.api.responses import accepted_task_response, created_response, error_response, no_content_response
from pilot.tasks.jobs.setup_production_task import SetupProductionTask

benches_bp = Blueprint("benches", __name__)
bench_readiness_bp = Blueprint("bench-readiness", __name__)

_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_ADMIN_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)


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


benches_bp.before_request(guard_bench_management)
bench_readiness_bp.before_request(guard_bench_management)


@benches_bp.get("")
def list_benches():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    benches_dir = bench_root.parent
    benches = []
    for bench_dir in sorted(benches_dir.iterdir()):
        if bench_dir.is_symlink() or not bench_dir.is_dir():
            continue
        try:
            benches.append(_bench_resource(bench_dir))
        except Exception:
            continue
    return jsonify(benches)


@benches_bp.get("/<name>")
def get_bench(name: str):
    if not _NAME_RE.fullmatch(name):
        return error_response("invalid_bench_name", "Invalid bench name.", 422)
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        bench_dir = _target_bench_dir(bench_root, name)
    except ValueError:
        return error_response("bench_not_found", f"Bench '{name}' not found.", 404)
    if not (bench_dir / "bench.toml").exists():
        return error_response("bench_not_found", f"Bench '{name}' not found.", 404)
    try:
        return jsonify(_bench_resource(bench_dir))
    except Exception:
        return error_response("bench_unavailable", "Could not read the bench.", 503)


def _bench_resource(bench_dir: Path) -> dict:
    toml_path = bench_dir / "bench.toml"
    config = BenchTomlStore(toml_path).read_raw()
    admin_config = config.get("admin", {})
    production_config = config.get("production", {})
    port = admin_config.get("port")
    if not isinstance(port, int) or isinstance(port, bool):
        raise ValueError("Bench admin port is unavailable")
    process_manager = production_config.get("process_manager", "")
    if not isinstance(process_manager, str):
        process_manager = ""
    process_manager = process_manager.lower()
    if process_manager in ("", "none"):
        process_manager = ""
    elif process_manager == "supervisord":
        process_manager = "supervisor"
    enabled = production_config.get("enabled")
    production = enabled if isinstance(enabled, bool) else process_manager != ""
    domain = admin_config.get("domain", "")
    if not isinstance(domain, str):
        domain = ""
    bench = BenchProvider(bench_dir)
    tls = admin_config.get("tls") is True
    scheme = "https" if tls and bench.has_admin_cert else "http"
    return {
        "name": bench_dir.name,
        "port": port,
        "domain": domain,
        "production": production,
        "process_manager": process_manager or None,
        "reachable": bench.is_port_open(port) or bench.is_port_open(port + 1),
        "admin_url": f"{scheme}://{domain}" if production and domain else "",
        "workload_running": bench.is_workload_running if production else None,
        "admin_running": bench.is_admin_running if production else None,
        "site_count": bench.site_count,
    }


def _target_bench_dir(bench_root: Path, name: str) -> Path:
    target = bench_root.parent / name
    if target.is_symlink() or target.resolve(strict=False).parent != bench_root.parent.resolve():
        raise ValueError("Invalid bench path")
    return target


def _bench_lock_target(bench_root: Path, name: str) -> Path:
    return bench_root.parent / f"{name}.lifecycle"


def _bench_management_lock_target(bench_root: Path) -> Path:
    return bench_root.parent / "bench-management.lock-target"


def _bench_busy_response(name: str):
    return error_response(
        "bench_busy",
        f"Bench '{name}' is busy with another operation.",
        409,
    )


@benches_bp.post("/<name>/actions/start")
def start_bench(name: str):
    return _run_action(name, "start")


@benches_bp.post("/<name>/actions/stop")
def stop_bench(name: str):
    return _run_action(name, "stop")


@benches_bp.post("/<name>/actions/restart")
def restart_bench(name: str):
    return _run_action(name, "restart")


@benches_bp.post("/<name>/actions/setup-production")
def setup_production_bench(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not _NAME_RE.fullmatch(name):
        return error_response("invalid_bench_name", "Invalid bench name.", 422)

    try:
        target_dir = _target_bench_dir(bench_root, name)
    except ValueError:
        return error_response("bench_not_found", f"Bench '{name}' not found.", 404)
    toml_path = target_dir / "bench.toml"
    if not toml_path.exists():
        return error_response("bench_not_found", f"Bench '{name}' not found.", 404)

    data = request.get_json(silent=True) or {}
    process_manager_raw = (data.get("process_manager") or "systemd").strip().lower()
    if process_manager_raw == "supervisord":
        process_manager_raw = "supervisor"
    
    from pilot.config.production import VALID_PROCESS_MANAGERS
    if process_manager_raw not in VALID_PROCESS_MANAGERS:
        return error_response(
            "invalid_process_manager",
            f"Choose a process manager: {', '.join(VALID_PROCESS_MANAGERS)}.",
            422,
        )
    process_manager = process_manager_raw

    admin_domain = (data.get("admin_domain") or "").strip()
    if not admin_domain:
        return error_response("admin_domain_required", "Admin domain is required.", 422)
    if not _ADMIN_DOMAIN_RE.match(admin_domain):
        return error_response(
            "invalid_admin_domain", f"'{admin_domain}' is not a valid hostname.", 422
        )

    from pilot.utils import host_owner
    owner = host_owner(target_dir, admin_domain)
    if owner:
        return error_response(
            "admin_domain_conflict",
            f"Admin domain '{admin_domain}' is already used by bench '{owner}'.",
            409,
        )

    tls = data.get("tls")
    if tls is not None and not isinstance(tls, bool):
        return error_response("invalid_tls", "tls must be a boolean.", 422)
    letsencrypt_email = data.get("letsencrypt_email")
    if tls is True:
        email_str = (letsencrypt_email or "").strip()
        if not email_str:
            return error_response(
                "letsencrypt_email_required",
                "Let's Encrypt email is required when TLS is enabled.",
                422,
            )
        letsencrypt_email = email_str

    from pilot.managers.platform import has_passwordless_sudo
    if not has_passwordless_sudo():
        return error_response(
            "privileged_operation_unavailable",
            "Production setup requires non-interactive system privileges.",
            409,
        )

    try:
        with (
            exclusive_file_lock(_bench_management_lock_target(bench_root), blocking=False),
            exclusive_file_lock(_bench_lock_target(bench_root, name), blocking=False),
        ):
            bench = Bench(target_dir)
            task_id = SetupProductionTask.queue(
                bench,
                process_manager=process_manager,
                admin_domain=admin_domain,
                tls=tls,
                letsencrypt_email=letsencrypt_email,
            )
            return accepted_task_response(target_dir, task_id)
    except BlockingIOError:
        return _bench_busy_response(name)
    except Exception:
        return error_response("setup_production_failed", "Could not initiate production setup.", 500)


def _run_action(name: str, action: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not _NAME_RE.fullmatch(name):
        return error_response("invalid_bench_name", "Invalid bench name.", 422)

    try:
        target_dir = _target_bench_dir(bench_root, name)
    except ValueError:
        return error_response("bench_not_found", f"Bench '{name}' not found.", 404)
    toml_path = target_dir / "bench.toml"
    if not toml_path.exists():
        return error_response("bench_not_found", f"Bench '{name}' not found.", 404)

    try:
        with (
            exclusive_file_lock(_bench_management_lock_target(bench_root), blocking=False),
            exclusive_file_lock(_bench_lock_target(bench_root, name), blocking=False),
        ):
            return _run_action_locked(target_dir, toml_path, name, action)
    except BlockingIOError:
        return _bench_busy_response(name)


def _run_action_locked(target_dir: Path, toml_path: Path, name: str, action: str):
    if not toml_path.exists():
        return error_response("bench_not_found", f"Bench '{name}' not found.", 404)
    try:
        target_config = BenchTomlStore(toml_path).read()
    except Exception:
        return error_response(
            "bench_unavailable",
            "Could not read the bench configuration.",
            503,
        )
    if not target_config.production.enabled:
        return error_response(
            "bench_action_unavailable",
            "Start, stop, and restart are only supported for production benches.",
            409,
        )
    try:
        manager = ProcessManager.for_bench(Bench(target_config, target_dir))
        operation = manager.start_workload if action == "start" else getattr(manager, action)
        operation()
        return jsonify(_bench_resource(target_dir))
    except Exception:
        return error_response(
            "bench_action_failed",
            "Could not complete the bench action.",
            500,
        )


@benches_bp.delete("/<name>")
def delete_bench(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not _NAME_RE.fullmatch(name):
        return error_response("invalid_bench_name", "Invalid bench name.", 422)

    try:
        target_dir = _target_bench_dir(bench_root, name)
    except ValueError:
        return error_response("bench_not_found", f"Bench '{name}' not found.", 404)
    toml_path = target_dir / "bench.toml"
    if not toml_path.exists():
        return error_response("bench_not_found", f"Bench '{name}' not found.", 404)
    if target_dir.resolve() == bench_root.resolve():
        return error_response("bench_drop_conflict", "The active bench cannot be dropped.", 409)

    try:
        with (
            exclusive_file_lock(_bench_management_lock_target(bench_root), blocking=False),
            exclusive_file_lock(_bench_lock_target(bench_root, name), blocking=False),
        ):
            return _delete_bench_locked(target_dir, toml_path, name)
    except BlockingIOError:
        return _bench_busy_response(name)


def _delete_bench_locked(target_dir: Path, toml_path: Path, name: str):
    if not toml_path.exists():
        return error_response("bench_not_found", f"Bench '{name}' not found.", 404)
    sites = BenchProvider(target_dir).site_count
    if sites:
        return error_response(
            "bench_not_empty",
            f"Bench '{name}' has {sites} site(s). Drop them first.",
            409,
        )

    try:
        target_config = BenchTomlStore(toml_path).read()
    except Exception:
        return error_response(
            "bench_unavailable",
            "Could not read the bench configuration.",
            503,
        )
    if target_config.production.enabled:
        from pilot.managers.platform import has_passwordless_sudo

        if not has_passwordless_sudo():
            return error_response(
                "privileged_operation_unavailable",
                "Dropping a production bench requires non-interactive system privileges.",
                409,
            )

    try:
        from pilot.managers.platform import noninteractive_privileges

        with noninteractive_privileges():
            Bench(target_config, target_dir).drop()
    except Exception:
        return error_response("bench_drop_failed", "Could not drop the bench.", 500)
    return no_content_response()


@benches_bp.get("/domain-options")
def get_domain_options():
    """Wildcard domain suffixes new bench admin domains may be built from."""
    from pilot.core.domains import DomainRouteProvider
    from pilot.utils import wildcard_suffix

    try:
        patterns = DomainRouteProvider.wildcard_domains()
    except Exception:
        return error_response(
            "wildcard_domains_unavailable", "Could not read wildcard domains.", 500
        )
    return jsonify({"domains": [wildcard_suffix(p) for p in patterns]})


@benches_bp.post("")
def create_bench():
    bench_root = Path(current_app.config["BENCH_ROOT"])

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
    if not name or not _NAME_RE.fullmatch(name):
        return error_response(
            "invalid_bench_name",
            "Bench name must contain only letters, numbers, '-' and '_'.",
            422,
        )

    from pilot.config.production import VALID_PROCESS_MANAGERS

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

    try:
        with (
            exclusive_file_lock(_bench_management_lock_target(bench_root), blocking=False),
            exclusive_file_lock(_bench_lock_target(bench_root, name), blocking=False),
        ):
            return _create_bench_locked(
                bench_root,
                name,
                process_manager,
                db_type,
                admin_domain,
                bool(data["admin_tls"]) if "admin_tls" in data else None,
            )
    except BlockingIOError:
        return _bench_busy_response(name)


def _create_bench_locked(
    bench_root: Path,
    name: str,
    process_manager: str,
    db_type: str,
    admin_domain: str,
    admin_tls: bool | None,
):
    from pilot.core.domains import DomainRouteProvider
    from pilot.utils import host_owner, matches_wildcard, normalize_host

    try:
        new_dir = _target_bench_dir(bench_root, name)
    except ValueError:
        return error_response(
            "bench_already_exists",
            f"Bench '{name}' already exists.",
            409,
        )
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

    patterns = DomainRouteProvider.wildcard_domains()
    if patterns and not matches_wildcard(admin_domain, patterns):
        return error_response(
            "invalid_admin_domain",
            f"Admin domain must match one of: {', '.join(patterns)}.",
            422,
        )

    production_parent = BenchProvider(bench_root).is_production
    if production_parent:
        from pilot.managers.platform import has_passwordless_sudo

        if not has_passwordless_sudo():
            return error_response(
                "privileged_operation_unavailable",
                "Production bench creation requires non-interactive system privileges.",
                409,
            )

    try:
        Bench.create_at(
            new_dir,
            name,
            process_manager=process_manager,
            admin_domain=admin_domain,
            admin_tls=admin_tls,
            db_type=db_type,
        )
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

    if production_parent:
        try:
            from pilot.managers.nginx import NginxManager
            from pilot.managers.platform import noninteractive_privileges

            with noninteractive_privileges():
                bench = Bench(BenchTomlStore.for_bench(new_dir).read(), new_dir)
                DomainRouteProvider(bench).register(admin_domain, admin_domain)
                from pilot.managers.processes.base import ManagedProcessManager

                configured_pm = bench.config.production.process_manager
                PM: type[ManagedProcessManager]
                if configured_pm == "systemd":
                    from pilot.managers.processes.systemd import SystemdProcessManager as PM
                else:
                    from pilot.managers.processes.supervisor import SupervisorProcessManager as PM
                pm = PM(bench)
                pm.start_admin()
                # Just enough to make the wizard reachable at its domain (over plain HTTP).
                # The workload, TLS, and production.enabled all need the venv/framework app
                # the wizard's init step installs, so WizardSetupTask finishes the rest via
                # SetupProductionCommand once that's done.
                nginx = NginxManager(bench)
                nginx.generate_config()
                nginx.install_config()
                server_ip = DomainRouteProvider._server_ip()
        except Exception:
            return error_response(
                "bench_start_failed",
                "The bench was created but its Admin could not be started.",
                500,
                {"created": True, "name": name},
            )
        return created_response(
            {
                **_bench_resource(new_dir),
                "wizard_at_domain": True,
                "scheme": "http",
                "server_ip": server_ip,
            },
            url_for("benches.get_bench", name=name),
        )

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
                "admin.backend.run_server",
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
        return error_response(
            "bench_start_failed",
            "The bench was created but its setup server could not be started.",
            500,
            {"created": True, "name": name},
        )
    return created_response(
        {
            **_bench_resource(new_dir),
            "wizard_at_domain": False,
        },
        url_for("benches.get_bench", name=name),
    )


@bench_readiness_bp.post("/bench-readiness-checks")
def create_readiness_check():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    if "domain" in data and not isinstance(data["domain"], str):
        return error_response("invalid_domain", "domain must be a string.", 422)
    if "scheme" in data and not isinstance(data["scheme"], str):
        return error_response("invalid_scheme", "scheme must be a string.", 422)

    domain = (data.get("domain") or "").strip()
    if domain:
        if "port" in data:
            return error_response(
                "invalid_readiness_check",
                "Provide either domain or port, not both.",
                422,
            )
        if not _ADMIN_DOMAIN_RE.fullmatch(domain):
            return error_response("invalid_domain", "domain must be a valid hostname.", 422)
        scheme = (data.get("scheme") or "http").strip()
        if scheme not in ("http", "https"):
            return error_response("invalid_scheme", "scheme must be 'http' or 'https'.", 422)
        return jsonify({"ready": BenchProvider(bench_root).is_wizard_ready(domain, scheme)})
    if "scheme" in data:
        return error_response(
            "invalid_readiness_check",
            "scheme is valid only with domain.",
            422,
        )
    port = data.get("port")
    if isinstance(port, bool) or not isinstance(port, int):
        return error_response("invalid_port", "port must be an integer.", 422)
    if not 1 <= port <= 65535:
        return error_response("invalid_port", "port must be between 1 and 65535.", 422)
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            pass
        return jsonify({"ready": True})
    except OSError:
        return jsonify({"ready": False})
