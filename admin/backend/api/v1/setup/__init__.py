from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from admin.backend.api.responses import accepted_task_response, error_response, no_content_response
from admin.backend.api.v1.setup.config import (
    apply_existing_local_database,
    read_defaults,
    validate_configuration,
    validate_settable_keys,
)
from admin.backend.api.v1.setup.database import database_validation, database_validation_state
from admin.backend.api.v1.setup.state import (
    clear_wizard_marker_if_idle,
    running_setup_task,
    setup_handoff_task,
    wizard_marker_path,
)
from pilot.config import BenchConfig
from pilot.config.bench import FRAMEWORK_BRANCHES
from pilot.core.bench import Bench
from pilot.exceptions import TaskConflictError, TaskNotFoundError
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked
from pilot.managers.task import TaskReader, TaskStatus
from pilot.tasks.wizard_setup import WizardSetupTask

setup_bp = Blueprint("setup", __name__)

__all__ = [
    "BenchConfig",
    "read_defaults",
    "running_setup_task",
    "setup_bp",
    "validate_configuration",
    "wizard_marker_path",
]


@setup_bp.get("/configuration")
def get_configuration():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    return jsonify(read_defaults(bench_root))


@setup_bp.get("/framework-branches")
def get_framework_branches():
    return jsonify({"branches": FRAMEWORK_BRANCHES})


@setup_bp.put("/configuration")
def update_configuration():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    if error := validate_settable_keys(data):
        return error_response("invalid_setup_configuration", error, 422)

    with exclusive_file_lock(bench_root / ".setup-configuration"):
        return _update_configuration(bench_root, data)


def _update_configuration(bench_root: Path, data: dict):
    try:
        current = BenchConfig.read_flat(bench_root)
    except Exception:
        return error_response(
            "configuration_unavailable",
            "Setup configuration is unavailable.",
            503,
        )

    data = apply_existing_local_database(bench_root, data)
    settings = {**current, **data, "admin_enabled": True}
    error = validate_configuration(settings)
    if error:
        return error_response("invalid_setup_configuration", error, 422)

    toml_path = bench_root / "bench.toml"
    try:
        BenchConfig.write_flat(
            bench_root,
            current.get("bench_name") or bench_root.name,
            {**data, "admin_enabled": True},
            port_offset=BenchConfig.current_port_offset(toml_path),
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

    return jsonify(read_defaults(bench_root))


@setup_bp.post("/database-validations")
def validate_database():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    try:
        engine, manager, password, existing = database_validation(
            bench_root=Path(current_app.config["BENCH_ROOT"]),
            data=data,
        )
        state = database_validation_state(manager, password, existing)
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


@setup_bp.post("/actions/start")
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
        config = BenchConfig.read(bench_root)
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
            existing = setup_handoff_task(bench_root)
            if existing:
                replace_private_text_locked(marker, existing.task_id)
                return accepted_task_response(bench_root, existing.task_id)
            replace_private_text_locked(marker, "")
            task_id = WizardSetupTask.queue(
                Bench(bench_root),
                idempotency_key=idempotency_key,
            )
            replace_private_text_locked(marker, task_id)
            return accepted_task_response(bench_root, task_id)
    except TaskConflictError as error:
        clear_wizard_marker_if_idle(bench_root)
        return error_response("task_conflict", str(error), 409)
    except ValueError as error:
        clear_wizard_marker_if_idle(bench_root)
        return error_response("invalid_setup_task", str(error), 422)
    except Exception:
        clear_wizard_marker_if_idle(bench_root)
        return error_response("setup_start_failed", "Could not start setup.", 500)


@setup_bp.post("/actions/finish")
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

    response = _validate_finished_setup_task(bench_root, task_id)
    if response is not None:
        return response

    response = no_content_response()
    if current_app.config.get("WIZARD_SERVER"):
        response.call_on_close(
            lambda: threading.Timer(
                0.1,
                lambda: os.kill(os.getpid(), signal.SIGTERM),
            ).start()
        )
    return response


def _validate_finished_setup_task(bench_root: Path, task_id: str):
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
        conflict = _check_marker_claims_this_task(bench_root, marker, task_id)
        if conflict is not None:
            return conflict
        if not Bench(bench_root).python.exists():
            return error_response(
                "setup_not_initialized",
                "Bench setup has not finished.",
                409,
            )
        marker.unlink(missing_ok=True)
    return None


def _check_marker_claims_this_task(bench_root: Path, marker: Path, task_id: str):
    """Caller must already hold the marker's exclusive lock."""
    if running_setup_task(bench_root):
        return error_response("setup_active", "Another setup task is still active.", 409)

    if not marker.exists():
        return None
    handoff = setup_handoff_task(bench_root)
    if handoff is None or handoff.task_id != task_id:
        return error_response(
            "setup_task_mismatch",
            "Task is not the current setup attempt.",
            409,
        )
    return None
