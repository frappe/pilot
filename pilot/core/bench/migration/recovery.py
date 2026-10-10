from __future__ import annotations

from datetime import UTC, datetime

from pilot.core.bench.migration.state import MigrationStateError
from pilot.internal.atomic_file import exclusive_file_lock
from pilot.internal.tasks.models import TaskStatus
from pilot.internal.tasks.process import TaskProcess
from pilot.internal.tasks.process_identity import ProcessOwnership
from pilot.internal.tasks.store import TaskStore

_INTERRUPTED = {TaskStatus.FAILED, TaskStatus.KILLED}


def _verify_task_stopped(processes, tasks, task_id: str) -> None:
    record = processes.read(task_id)
    if record is not None:
        ownership = processes.ownership(task_id)
        if ownership not in (ProcessOwnership.DEAD, ProcessOwnership.STALE):
            raise MigrationStateError(f"Task process ownership is {ownership.value}")
        if processes._inspector.owned_pids(record.identity):
            raise MigrationStateError("Task descendants are still running")
        return
    markers = ["pilot.internal.tasks.wrapper", str(tasks.task_dir(task_id))]
    try:
        pid = processes._inspector.get_pid_matching(markers, tasks.read_pid(task_id))
    except OSError as error:
        raise MigrationStateError("Cannot verify task process absence") from error
    if pid is not None:
        raise MigrationStateError("Task wrapper is still running")


def _mark_interrupted_phase(operation, phase: str, site_name: str | None) -> None:
    if phase == "migrating":
        if not site_name:
            raise MigrationStateError("Migration task does not identify a site")
        site = operation.site(site_name)
        site.migration_status = "failed"
        operation._union_touched_tables(site)
    elif phase == "backing_up":
        if not site_name:
            raise MigrationStateError("Backup task does not identify a site")
        operation.site(site_name).backup_status = "failed"


def reconcile_migration(bench, operation_id: str, *, force: bool = False):
    """Mark an interrupted chain as needing attention only after checking ownership."""
    tasks = TaskStore(bench.path)
    processes = TaskProcess(bench.path)
    with exclusive_file_lock(bench.migrations.root / f".{operation_id}.recovery"):
        operation = bench.migrations.get(operation_id)
        if operation.is_resolved or operation.state == "needs_attention":
            return operation
        if operation.state not in ("preparing", "backing_up", "updating", "migrating"):
            raise MigrationStateError(f"Cannot reconcile {operation.state}")

        if not operation.chain:
            raise MigrationStateError("No recorded task chain to verify")
        entry = operation.chain[-1]
        task_id = entry["task_id"]
        status = tasks.read_status(task_id)
        if status not in _INTERRUPTED:
            raise MigrationStateError(f"Task {task_id} is {status.value}, not interrupted")

        _verify_task_stopped(processes, tasks, task_id)
        phase = operation.state.name
        site_name = entry.get("site")
        _mark_interrupted_phase(operation, phase, site_name)
        operation.diagnosis = {
            "phase": phase,
            "message": f"Task {task_id} ended ({status.value}) without finalizing its migration.",
            "output_excerpt": "",
        }
        operation.recovery_events.append({
            "action": "reconcile_interrupted",
            "task_id": task_id,
            "at": datetime.now(UTC).isoformat(),
            "forced": force,
        })
        operation._enter_needs_attention(phase, site_name)
        bench.audit_action("migration", {
            "event": "reconcile_interrupted",
            "operation_id": operation_id,
            "task_id": task_id,
            "forced": force,
        })
        return operation


def reconcile_orphaned_migrations(bench) -> list[str]:
    """Best-effort reconciliation of failed or killed tasks after worker recovery."""
    changed = []
    for operation in bench.migrations.get_all():
        if operation.is_resolved or operation.state.is_failure or not operation.chain:
            continue
        try:
            task_id = operation.chain[-1]["task_id"]
            if TaskStore(bench.path).read_status(task_id) not in _INTERRUPTED:
                continue
            reconcile_migration(bench, operation.id)
        except (OSError, ValueError, KeyError, MigrationStateError):
            continue
        changed.append(operation.id)
    return changed
