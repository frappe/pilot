from __future__ import annotations

import typing
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request

from admin.backend.api_contract import error_response, no_content_response
from admin.backend.auth import allow_during_setup, set_session_cookie
from pilot.tasks.manager.task_reader import TaskReader
from pilot.tasks.manager.task_runner import TaskRunner
from pilot.tasks.manager.task_state import ACTIVE_TASK_STATUSES, TaskStatus
from admin.backend.task_response import accepted_task_response
from admin.backend.validators import validate_branch_name, validate_repo_url
from pilot.config.bench_toml_builder import (
    FRAMEWORK_BRANCHES,
    BenchTomlBuilder,
    current_port_offset,
)
from pilot.config.toml_store import BenchTomlStore
from pilot.exceptions import TaskConflictError, TaskNotFoundError
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked

if typing.TYPE_CHECKING:
    from pilot.managers.mariadb_manager import MariaDBManager
    from pilot.managers.postgres_manager import PostgresManager

setup_bp = Blueprint("setup", __name__)


def wizard_marker_path(bench_root: Path) -> Path:
    """Marker that the bench is going through first-time setup via the wizard.

    Written when the wizard kicks off its setup task and cleared when setup
    finishes (and as a safety-net by /api/v1/bootstrap once the bench is fully set up).
    It keeps /api/v1/bootstrap on the wizard while init runs — env/bin/python can appear
    partway through, making the bench look 'initialized' before the task is done —
    so a reload returns to the wizard rather than a half-built dashboard.
    """
    return bench_root / ".wizard-active"


@setup_bp.get("/configuration")
@allow_during_setup
def get_configuration():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    return jsonify(_read_defaults(bench_root))


@setup_bp.get("/framework-branches")
@allow_during_setup
def get_framework_branches():
    return jsonify({"branches": FRAMEWORK_BRANCHES})


@setup_bp.put("/configuration")
@allow_during_setup
def update_configuration():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)

    with exclusive_file_lock(bench_root / ".setup-configuration"):
        return _update_configuration(bench_root, data)


def _update_configuration(bench_root: Path, data: dict):
    store = BenchTomlStore.for_bench(bench_root)
    current = {}
    if store.exists():
        try:
            current = store.read_flat()
        except Exception:
            return error_response(
                "configuration_unavailable",
                "Setup configuration is unavailable.",
                503,
            )
    if current.get("admin_password") and g.jwt_claims is None:
        return error_response(
            "authentication_required",
            "Authentication is required.",
            401,
        )

    settings = {**current, **data, "admin_enabled": True}
    error = _validate(settings)
    if error:
        return error_response("invalid_setup_configuration", error, 422)

    toml_path = bench_root / "bench.toml"
    try:
        store.write_flat(
            current.get("bench_name") or bench_root.name,
            {**data, "admin_enabled": True},
            port_offset=current_port_offset(toml_path),
        )
    except (TypeError, ValueError):
        return error_response(
            "invalid_setup_configuration",
            "Setup configuration contains invalid fields.",
            422,
        )
    except Exception:
        return error_response(
            "configuration_update_failed",
            "Could not update setup configuration.",
            500,
        )

    resp = jsonify(_read_defaults(bench_root))
    if settings.get("admin_password"):
        _issue_setup_session(resp, toml_path)
    return resp


def _issue_setup_session(resp, toml_path: Path) -> None:
    from pilot.commands.generate_session import ensure_jwt_secret, issue_token

    set_session_cookie(
        resp,
        issue_token(ensure_jwt_secret(toml_path)),
        current_app.config["SESSION_COOKIE_SECURE"],
    )


@setup_bp.post("/database-validations")
@allow_during_setup
def validate_database():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    try:
        engine, manager, password, existing = _database_validation(
            bench_root=Path(current_app.config["BENCH_ROOT"]),
            data=data,
        )
        state = _database_validation_state(manager, password, existing)
    except ValueError as error:
        return error_response(
            "invalid_database_configuration",
            str(error),
            422,
        )
    except Exception:
        return error_response(
            "database_validation_failed",
            "Could not validate the database configuration.",
            500,
        )
    return jsonify({"engine": engine, "state": state})


def _database_validation(bench_root: Path, data: dict):
    engine = data.get("engine")
    if engine not in ("mariadb", "postgres"):
        raise ValueError("engine must be 'mariadb' or 'postgres'.")

    for field in ("password", "admin_user", "host"):
        if field in data and not isinstance(data[field], str):
            raise ValueError(f"{field} must be a string.")
    if "existing" in data and not isinstance(data["existing"], bool):
        raise ValueError("existing must be a boolean.")

    default_port = 3306 if engine == "mariadb" else 5432
    port = data.get("port", default_port)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535.")

    password = data.get("password", "")
    default_admin_user = "root" if engine == "mariadb" else "postgres"
    admin_user = (data.get("admin_user") or default_admin_user).strip()
    host = (data.get("host") or "localhost").strip()
    existing = data.get("existing", False)
    if existing and (not host or not admin_user):
        raise ValueError("host and admin_user are required for an existing server.")

    if engine == "mariadb":
        from pilot.managers.mariadb_manager import MariaDBManager

        config = _mariadb_config(
            bench_root,
            password,
            admin_user,
            host,
            port,
            existing,
        )
        return engine, MariaDBManager(config), password, existing

    from pilot.config.postgres_config import PostgresConfig
    from pilot.managers.postgres_manager import PostgresManager

    config = PostgresConfig(
        host=host,
        port=port,
        root_password=password,
        admin_user=admin_user,
        existing=existing,
    )
    return engine, PostgresManager(config), password, existing


def _database_validation_state(manager, password: str, existing: bool) -> str:
    if existing:
        return "valid" if manager.check_credentials(password) else "invalid"
    if _is_fresh_install(manager):
        return "will_install"
    return "valid" if manager.check_credentials(password) else "invalid"


def _is_fresh_install(manager: PostgresManager | MariaDBManager) -> bool:
    """True when init will install/provision + secure the server itself
    (rather than connecting to an already-configured one). is_provisioned()
    checks for the manager's own systemd --user unit — the single source of
    truth for whether this bench user's server has already been set up."""
    if not manager.is_installed():
        return True
    return not manager.is_provisioned()


def _mariadb_config(
    bench_root: Path,
    password: str,
    admin_user: str = "root",
    host: str = "",
    port=None,
    existing: bool = False,
):
    """Build a MariaDBConfig from the bench's toml with the entered credentials applied."""
    from pilot.config.mariadb_config import MariaDBConfig

    config = MariaDBConfig(
        root_password=password,
        admin_user=admin_user,
        host=host or "localhost",
        port=int(port or 3306),
        existing=existing,
    )
    toml_path = bench_root / "bench.toml"
    if toml_path.exists():
        try:
            settings = BenchTomlStore(toml_path).read_flat()
            config.socket_path = settings.get("mariadb_socket_path", "") or ""
        except Exception:
            pass
    return config


def _validate(data: dict) -> str | None:
    text_fields = (
        "admin_password",
        "app_branch",
        "app_repo",
        "db_type",
        "mariadb_admin_user",
        "mariadb_host",
        "mariadb_password",
        "postgres_admin_user",
        "postgres_host",
        "postgres_password",
    )
    for field in text_fields:
        if field in data and not isinstance(data[field], str):
            return f"{field} must be a string"
    for field in ("mariadb_existing", "postgres_existing"):
        if field in data and not isinstance(data[field], bool):
            return f"{field} must be a boolean"
    for field in ("mariadb_port", "postgres_port"):
        value = data.get(field)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 65535
        ):
            return f"{field} must be an integer between 1 and 65535"

    if not data.get("admin_password"):
        return "admin_password is required"
    db_type = data.get("db_type", "mariadb")
    if db_type not in ("mariadb", "postgres"):
        return "db_type must be 'mariadb' or 'postgres'"
    if db_type == "mariadb" and not data.get("mariadb_password"):
        return "mariadb_password is required"
    if db_type == "postgres" and not data.get("postgres_password"):
        return "postgres_password is required"
    if data.get(f"{db_type}_existing"):
        if not data.get(f"{db_type}_host"):
            return f"{db_type}_host is required when connecting to an existing database server"
        if not data.get(f"{db_type}_admin_user"):
            return (
                f"{db_type}_admin_user is required when connecting to an existing database server"
            )
    if "app_repo" in data and (error := validate_repo_url(data["app_repo"])):
        return error
    if "app_branch" in data and (error := validate_branch_name(data["app_branch"])):
        return error
    return None


@setup_bp.post("/actions/start")
@allow_during_setup
def start_setup():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    idempotency_key = request.headers.get("Idempotency-Key")
    if not idempotency_key:
        return error_response(
            "idempotency_key_required",
            "Idempotency-Key is required.",
            422,
        )
    try:
        config = BenchTomlStore.for_bench(bench_root).read()
        config.validate()
    except Exception:
        return error_response(
            "invalid_setup_configuration",
            "Setup configuration is invalid.",
            422,
        )

    marker = wizard_marker_path(bench_root)
    try:
        with exclusive_file_lock(marker):
            existing = _setup_handoff_task(bench_root)
            if existing:
                replace_private_text_locked(marker, existing.task_id)
                return accepted_task_response(bench_root, existing.task_id)
            replace_private_text_locked(marker, "")
            task_id = TaskRunner(bench_root).run(
                "wizard-setup",
                {},
                idempotency_key=idempotency_key,
            )
            replace_private_text_locked(marker, task_id)
            return accepted_task_response(bench_root, task_id)
    except TaskConflictError as error:
        _clear_wizard_marker_if_idle(bench_root)
        return error_response("task_conflict", str(error), 409)
    except ValueError as error:
        _clear_wizard_marker_if_idle(bench_root)
        return error_response("invalid_setup_task", str(error), 422)
    except Exception:
        _clear_wizard_marker_if_idle(bench_root)
        return error_response("setup_start_failed", "Could not start setup.", 500)


@setup_bp.post("/actions/finish")
@allow_during_setup
def finish_setup():
    import os
    import signal
    import threading

    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    task_id = data.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return error_response("invalid_task", "task_id is required.", 422)

    try:
        task = TaskReader(bench_root).read_task(task_id)
    except TaskNotFoundError as error:
        return error_response("task_not_found", str(error), 404)
    except Exception:
        return error_response("task_unavailable", "Could not read setup task.", 500)

    if task.command != "wizard-setup":
        return error_response("setup_task_required", "Task is not a setup task.", 409)
    if task.status != TaskStatus.SUCCESS:
        return error_response(
            "setup_not_complete",
            "Setup task has not completed successfully.",
            409,
        )
    marker = wizard_marker_path(bench_root)
    with exclusive_file_lock(marker):
        handoff = _setup_handoff_task(bench_root)
        if handoff is None or handoff.task_id != task_id:
            return error_response(
                "setup_task_mismatch",
                "Task is not the current setup attempt.",
                409,
            )
        if _running_setup_task(bench_root):
            return error_response(
                "setup_active",
                "Another setup task is still active.",
                409,
            )
        if not (bench_root / "config" / "Procfile").exists():
            return error_response(
                "setup_not_initialized",
                "Bench setup has not finished.",
                409,
            )
        marker.unlink(missing_ok=True)
    response = no_content_response()
    if current_app.config.get("WIZARD_SERVER"):
        response.call_on_close(
            lambda: threading.Timer(
                0.1,
                lambda: os.kill(os.getpid(), signal.SIGTERM),
            ).start()
        )
    return response


_PASSWORD_KEYS = ("admin_password", "mariadb_password", "postgres_password")


def _read_defaults(bench_root: Path) -> dict:
    from pilot.platform import is_linux, native_process_manager

    # This is a read endpoint the wizard polls before login — it must never echo
    # a DB password back, default or real, whether or not bench.toml has one set.
    defaults = {
        key: value
        for key, value in BenchTomlBuilder.DEFAULTS.items()
        if key not in _PASSWORD_KEYS
    }

    result = {
        "bench_name": bench_root.name,
        "is_linux": is_linux(),
        "native_process_manager": native_process_manager(),
        **defaults,
    }
    toml_path = bench_root / "bench.toml"
    if toml_path.exists():
        try:
            settings = BenchTomlStore(toml_path).read_flat()
            for key in _PASSWORD_KEYS:
                result[f"{key}_configured"] = bool(settings.get(key))
                settings.pop(key, None)
            result.update(settings)
            if not result.get("bench_name"):
                result["bench_name"] = bench_root.name
        except Exception:
            pass

    for key in _PASSWORD_KEYS:
        result.setdefault(f"{key}_configured", False)

    try:
        task = _setup_handoff_task(bench_root)
        result["running_setup_task_id"] = task.task_id if task else None
    except Exception:
        result["running_setup_task_id"] = None

    return result


def _running_setup_task(bench_root: Path):
    return next(
        (
            t
            for t in TaskReader(bench_root).list_tasks(limit=None)
            if t.command == "wizard-setup" and t.status in ACTIVE_TASK_STATUSES
        ),
        None,
    )


def _setup_handoff_task(bench_root: Path):
    marker = wizard_marker_path(bench_root)
    if not marker.exists():
        return _running_setup_task(bench_root)

    task_id = marker.read_text(encoding="utf-8").strip()
    if task_id:
        try:
            task = TaskReader(bench_root).read_task(task_id)
        except TaskNotFoundError:
            return _running_setup_task(bench_root)
        if task.command == "wizard-setup" and task.status in {
            *ACTIVE_TASK_STATUSES,
            TaskStatus.SUCCESS,
        }:
            return task
        return None

    return next(
        (
            task
            for task in TaskReader(bench_root).list_tasks(limit=None)
            if task.command == "wizard-setup"
            and task.status in {*ACTIVE_TASK_STATUSES, TaskStatus.SUCCESS}
        ),
        None,
    )


def _clear_wizard_marker_if_idle(bench_root: Path) -> None:
    marker = wizard_marker_path(bench_root)
    try:
        with exclusive_file_lock(marker):
            if _running_setup_task(bench_root) is None:
                marker.unlink(missing_ok=True)
    except Exception:
        pass
