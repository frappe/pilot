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
from pilot.core.bench.migration import MigrationOperation
from pilot.exceptions import BenchError
from pilot.tasks.bypass_patch import BypassPatchTask
from pilot.tasks.retry_update import RetryUpdateTask
from pilot.tasks.revert_migration import RevertMigrationTask
from pilot.tasks.update import UpdateTask

migrations_bp = Blueprint("migrations", __name__)


def _bench() -> Bench:
    return Bench(Path(current_app.config["BENCH_ROOT"]))


def _resource_keys(operation: MigrationOperation) -> list[str]:
    """Claim the bench-update resource plus every affected site."""
    return ["bench:update", *[f"site:{site.name.lower()}" for site in operation.sites]]


def _summary(operation: MigrationOperation) -> dict:
    data = operation.to_dict()
    root = operation.root_task_id
    data["task_log_url"] = url_for("tasks.get_task", task_id=root) if root else None
    data["can_revert"] = operation.can_revert
    return data


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
    skip = bool(body.get("skip_failing_patches"))
    operation = bench.migrations.create_update(set(apps) if apps else None, skip)
    if bool(body.get("disable_safeguards")):
        operation.safeguards_disabled = True
        operation.store.save(operation)
    return _queue_root(bench, operation, UpdateTask, apps=apps, skip_failing_patches=skip)


def _queue_root(bench: Bench, operation: MigrationOperation, task_type, **task_args):
    try:
        task_id = task_type.queue(
            bench,
            operation_id=operation.id,
            resource_key=_resource_keys(operation),
            **task_args,
        )
    except Exception as error:
        bench.migrations.delete(operation.id)
        return task_failure(error)
    operation.root_task_id = task_id
    operation.store.save(operation)
    return _accepted(operation, task_id)


@migrations_bp.get("/migrations")
def list_migrations():
    limit, offset = parse_pagination(20, 100)
    status = request.args.get("status")
    kind = request.args.get("kind")
    site = request.args.get("site")
    operations = [
        _summary(operation)
        for operation in _bench().migrations.list()
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


@migrations_bp.post("/migrations/<operation_id>/actions/revert")
def revert_action(operation_id: str):
    operation = _load(operation_id)
    if operation is None:
        return _not_found()
    if operation.state not in ("needs_attention", "revert_failed"):
        return _invalid_state(operation)
    if not operation.can_revert:
        return error_response(
            "revert_unavailable", "Revert is unavailable: no safeguards were created.", 409
        )
    return _queue_action(operation, RevertMigrationTask, "revert")


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
    return _queue_action(operation, BypassPatchTask, "bypass_patch", patch=patch.strip())


def _queue_action(operation: MigrationOperation, task_type, role: str, **task_args):
    try:
        task_id = task_type.queue(
            operation.bench,
            operation_id=operation.id,
            resource_key=_resource_keys(operation),
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
    except BenchError:
        return None


def _not_found():
    return error_response("migration_not_found", "Migration operation not found.", 404)


def _invalid_state(operation: MigrationOperation):
    return error_response(
        "invalid_operation_state",
        f"Action is not allowed while the operation is {operation.state}.",
        409,
    )
