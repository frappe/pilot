from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from admin.backend.api.responses import error_response, no_content_response
from admin.backend.api.v1.benches.create import create_bench_locked
from admin.backend.api.v1.benches.readiness import bench_readiness_bp
from admin.backend.api.v1.benches.support import (
    ADMIN_DOMAIN_RE,
    BENCH_NAME_RE,
    bench_busy_response,
    bench_lock_target,
    bench_management_lock_target,
    bench_resource,
    guard_bench_management,
    target_bench_dir,
)
from admin.backend.providers.bench import BenchProvider
from pilot.config import BenchConfig
from pilot.core.bench import Bench
from pilot.internal.atomic_file import exclusive_file_lock

benches_bp = Blueprint("benches", __name__)

__all__ = ["bench_readiness_bp", "benches_bp"]


benches_bp.before_request(guard_bench_management)


@benches_bp.get("")
def list_benches():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    benches_dir = bench_root.parent
    benches = []
    for bench_dir in sorted(benches_dir.iterdir()):
        if bench_dir.is_symlink() or not bench_dir.is_dir():
            continue
        try:
            benches.append(bench_resource(bench_dir))
        except Exception:
            continue
    return jsonify(benches)


@benches_bp.get("/<name>")
def get_bench(name: str):
    if not BENCH_NAME_RE.fullmatch(name):
        return error_response("invalid_bench_name", "Invalid bench name.", 422)
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        bench_dir = target_bench_dir(bench_root, name)
    except ValueError:
        return error_response("bench_not_found", f"Bench '{name}' not found.", 404)
    if not (bench_dir / "bench.toml").exists():
        return error_response("bench_not_found", f"Bench '{name}' not found.", 404)
    try:
        return jsonify(bench_resource(bench_dir))
    except Exception:
        return error_response("bench_unavailable", "Could not read the bench.", 503)


@benches_bp.post("/<name>/actions/start")
def start_bench(name: str):
    return _run_action(name, "start")


@benches_bp.post("/<name>/actions/stop")
def stop_bench(name: str):
    return _run_action(name, "stop")


@benches_bp.post("/<name>/actions/restart")
def restart_bench(name: str):
    return _run_action(name, "restart")


def _run_action(name: str, action: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not BENCH_NAME_RE.fullmatch(name):
        return error_response("invalid_bench_name", "Invalid bench name.", 422)

    try:
        target_dir = target_bench_dir(bench_root, name)
    except ValueError:
        return error_response("bench_not_found", f"Bench '{name}' not found.", 404)
    toml_path = target_dir / "bench.toml"
    if not toml_path.exists():
        return error_response("bench_not_found", f"Bench '{name}' not found.", 404)

    try:
        with (
            exclusive_file_lock(bench_management_lock_target(bench_root), blocking=False),
            exclusive_file_lock(bench_lock_target(bench_root, name), blocking=False),
        ):
            return _run_action_locked(target_dir, toml_path, name, action)
    except BlockingIOError:
        return bench_busy_response(name)


def _run_action_locked(target_dir: Path, toml_path: Path, name: str, action: str):
    if not toml_path.exists():
        return error_response("bench_not_found", f"Bench '{name}' not found.", 404)
    try:
        target_config = BenchConfig.read(toml_path)
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
        Bench(target_config, target_dir).run_production_action(action)
        return jsonify(bench_resource(target_dir))
    except Exception:
        return error_response(
            "bench_action_failed",
            "Could not complete the bench action.",
            500,
        )


@benches_bp.delete("/<name>")
def delete_bench(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not BENCH_NAME_RE.fullmatch(name):
        return error_response("invalid_bench_name", "Invalid bench name.", 422)

    try:
        target_dir = target_bench_dir(bench_root, name)
    except ValueError:
        return error_response("bench_not_found", f"Bench '{name}' not found.", 404)
    toml_path = target_dir / "bench.toml"
    if not toml_path.exists():
        return error_response("bench_not_found", f"Bench '{name}' not found.", 404)
    if target_dir.resolve() == bench_root.resolve():
        return error_response("bench_drop_conflict", "The active bench cannot be dropped.", 409)

    try:
        with (
            exclusive_file_lock(bench_management_lock_target(bench_root), blocking=False),
            exclusive_file_lock(bench_lock_target(bench_root, name), blocking=False),
        ):
            return _delete_bench_locked(target_dir, toml_path, name)
    except BlockingIOError:
        return bench_busy_response(name)


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
        target_config = BenchConfig.read(toml_path)
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
    from pilot.core.adapters.domain_provider import DomainRouteProvider
    from pilot.utils import wildcard_suffix

    try:
        patterns = DomainRouteProvider.wildcard_domains()
    except Exception:
        return error_response("wildcard_domains_unavailable", "Could not read wildcard domains.", 500)
    return jsonify({"domains": [wildcard_suffix(p) for p in patterns]})


@benches_bp.post("")
def create_bench():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)

    request_data, response = _create_bench_request(data)
    if response is not None:
        return response

    name = request_data["name"]

    try:
        with (
            exclusive_file_lock(bench_management_lock_target(bench_root), blocking=False),
            exclusive_file_lock(bench_lock_target(bench_root, name), blocking=False),
        ):
            return create_bench_locked(bench_root, **request_data)
    except BlockingIOError:
        return bench_busy_response(name)


def _create_bench_request(data):
    if not isinstance(data, dict):
        return {}, error_response("malformed_request", "Expected a JSON object.", 400)
    if any(
        value is not None and not isinstance(value, str)
        for value in (
            data.get("name"),
            data.get("process_manager"),
            data.get("db_type"),
            data.get("admin_domain"),
        )
    ):
        return {}, error_response("invalid_bench", "Bench fields must be strings.", 422)
    if "admin_tls" in data and not isinstance(data["admin_tls"], bool):
        return {}, error_response("invalid_admin_tls", "admin_tls must be a boolean.", 422)

    name = (data.get("name") or "").strip()
    if not name or not BENCH_NAME_RE.fullmatch(name):
        return {}, error_response(
            "invalid_bench_name",
            "Bench name must contain only letters, numbers, '-' and '_'.",
            422,
        )

    process_manager, response = _process_manager_request(data)
    if response is not None:
        return {}, response

    db_type = (data.get("db_type") or "mariadb").strip()
    if db_type not in ("mariadb", "postgres", "sqlite"):
        return {}, error_response(
            "invalid_database",
            "Database must be 'mariadb', 'postgres', or 'sqlite'.",
            422,
        )

    admin_domain, response = _admin_domain_request(data)
    if response is not None:
        return {}, response

    return {
        "name": name,
        "process_manager": process_manager,
        "db_type": db_type,
        "admin_domain": admin_domain,
        "admin_tls": bool(data["admin_tls"]) if "admin_tls" in data else None,
    }, None


def _admin_domain_request(data):
    admin_domain = (data.get("admin_domain") or "").strip()
    if not admin_domain:
        return "", error_response(
            "admin_domain_required",
            "Admin domain is required so the bench is reachable in production.",
            422,
        )
    if ADMIN_DOMAIN_RE.match(admin_domain):
        return admin_domain, None
    return "", error_response("invalid_admin_domain", f"'{admin_domain}' is not a valid hostname.", 422)


def _process_manager_request(data):
    from pilot.config import VALID_PROCESS_MANAGERS

    process_manager = (data.get("process_manager") or "").strip().lower()
    if process_manager == "supervisord":
        process_manager = "supervisor"
    if process_manager in VALID_PROCESS_MANAGERS:
        return process_manager, None
    return "", error_response(
        "invalid_process_manager",
        f"Choose a process manager: {', '.join(VALID_PROCESS_MANAGERS)}.",
        422,
    )
