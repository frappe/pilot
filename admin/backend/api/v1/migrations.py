from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, url_for

from admin.backend.api.responses import (
    accepted_response,
    error_response,
    paginated_response,
    parse_pagination,
)
from admin.backend.api.v1.sites.shared import task_failure
from pilot.core.bench import Bench
from pilot.core.bench.migration.operation import MigrationOperation
from pilot.exceptions import MigrationNotFoundError
from pilot.tasks.bypass_patch import BypassPatchTask
from pilot.tasks.retry_update import RetryUpdateTask
from pilot.tasks.revert_migration import RevertMigrationTask

migrations_bp = Blueprint("migrations", __name__)


def _bench() -> Bench:
    return Bench(Path(current_app.config["BENCH_ROOT"]))


def _summary(operation: MigrationOperation) -> dict:
    data = operation.to_dict()
    for app, revision in zip(data["apps"], operation.apps, strict=True):
        app["compare_url"] = revision.compare_url
    data["can_restore"] = operation.can_revert
    data["task_logs"] = _task_logs(operation)
    return data


_CHAIN_LABELS = {
    "migration-backup": "Backup",
    "update": "Update apps",
    "migrate": "Migrate",
    "revert-apps": "Revert apps",
    "revert-site": "Recover site",
    "restart-services": "Restart services",
}


def _task_logs(operation: MigrationOperation) -> list[dict]:
    """Retained chain-task logs as a flat list, with attempt numbers."""
    logs: list[dict] = []
    attempts: dict[tuple, int] = {}
    for record in operation.chain:
        base = _CHAIN_LABELS.get(record.get("command", ""))
        task_id = record.get("task_id")
        if base is None or not isinstance(task_id, str):
            continue
        if not (operation.bench.path / "tasks" / task_id).is_dir():
            continue
        site = record.get("site")
        key = (record["command"], site)
        attempts[key] = attempts.get(key, 0) + 1
        label = base if attempts[key] == 1 else f"{base} (attempt {attempts[key]})"
        logs.append({"id": task_id, "label": label, "site": site})
    return logs


def _accepted(operation: MigrationOperation, task_id: str):
    return accepted_response(
        {"operation": _summary(operation), "task_id": task_id},
        url_for("migrations.get_migration", operation_id=operation.id),
    )


@migrations_bp.post("/updates")
def create_update():
    bench = _bench()
    body = request.get_json(silent=True) or {}
    apps = body.get("apps") or None
    operation = bench.migrations.create_update(set(apps) if apps else None)
    if bool(body.get("disable_safeguards")):
        operation.safeguards_disabled = True
        operation.store.save(operation)
    return _begin(bench, operation)


def _begin(bench: Bench, operation: MigrationOperation):
    """Start the operation's task chain, cleaning up the record if queuing fails."""
    try:
        task_id = operation.begin()
    except Exception as error:
        bench.migrations.delete(operation.id)
        return task_failure(error)
    return _accepted(operation, task_id)


@migrations_bp.get("/migrations")
def list_migrations():
    limit, offset = parse_pagination(20, 100)
    status = request.args.get("status")
    kind = request.args.get("kind")
    site = request.args.get("site")
    operations = [
        _summary(operation)
        for operation in _bench().migrations.get_all()
        if _matches(operation, status, kind, site)
    ]
    return paginated_response(lambda count: operations[:count], limit, offset)


def _matches(operation: MigrationOperation, status, kind, site) -> bool:
    if status and operation.state != status:
        return False
    if kind and operation.kind != kind:
        return False
    if site and site not in [entry.name for entry in operation.sites]:
        return False
    return True


@migrations_bp.get("/migrations/current")
def current_migration():
    operation = _bench().migrations.current()
    return jsonify(_summary(operation) if operation else None)


@migrations_bp.get("/migrations/<operation_id>")
def get_migration(operation_id: str):
    operation = _load(operation_id)
    if operation is None:
        return _not_found()
    return jsonify(_summary(operation))


@migrations_bp.post("/migrations/<operation_id>/actions/retry")
def retry_action(operation_id: str):
    operation = _load(operation_id)
    if operation is None:
        return _not_found()
    if operation.state != "needs_attention":
        return _invalid_state(operation)
    return _queue_action(operation, RetryUpdateTask, "retry")


@migrations_bp.post("/migrations/<operation_id>/actions/restore")
def restore_action(operation_id: str):
    operation = _load(operation_id)
    if operation is None:
        return _not_found()
    if operation.state not in ("needs_attention", "revert_failed"):
        return _invalid_state(operation)
    if not operation.can_revert:
        return error_response(
            "restore_unavailable", "Restore is unavailable: no safeguards were created.", 409
        )
    return _queue_action(operation, RevertMigrationTask, "restore")


@migrations_bp.post("/migrations/<operation_id>/actions/bypass-patch")
def bypass_patch_action(operation_id: str):
    operation = _load(operation_id)
    if operation is None:
        return _not_found()
    if operation.state != "needs_attention":
        return _invalid_state(operation)
    patch = (request.get_json(silent=True) or {}).get("patch")
    if not isinstance(patch, str) or not patch.strip():
        return error_response("invalid_fields", "A patch identifier is required.", 422)
    patch = patch.strip()
    if patch != (operation.diagnosis or {}).get("patch"):
        return error_response(
            "patch_mismatch",
            "The patch no longer matches the diagnosed migration failure.",
            409,
        )
    return _queue_action(operation, BypassPatchTask, "bypass_patch", patch=patch)


def _queue_action(operation: MigrationOperation, task_type, role: str, **task_args):
    try:
        task_id = task_type.queue(
            operation.bench,
            operation_id=operation.id,
            resource_key=operation.resource_keys,
            **task_args,
        )
    except Exception as error:
        return task_failure(error)
    operation.task_ids[role] = task_id
    operation.store.save(operation)
    return _accepted(operation, task_id)


def _load(operation_id: str) -> MigrationOperation | None:
    try:
        return _bench().migrations.get(operation_id)
    except MigrationNotFoundError:
        return None


def _not_found():
    return error_response("migration_not_found", "Migration operation not found.", 404)


def _invalid_state(operation: MigrationOperation):
    return error_response(
        "invalid_operation_state",
        f"Action is not allowed while the operation is {operation.state}.",
        409,
    )
