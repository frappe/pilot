from unittest.mock import MagicMock, patch

import pytest

from pilot.core.bench.migration.recovery import reconcile_migration
from pilot.core.bench.migration.state import MigrationStateError, get_state
from pilot.internal.tasks.models import TaskStatus
from pilot.internal.tasks.process_identity import ProcessOwnership


def _bench(tmp_path, task_status, ownership=ProcessOwnership.DEAD):
    bench = MagicMock()
    bench.path = tmp_path
    operation = MagicMock()
    operation.is_resolved = False
    operation.state = get_state("migrating")
    operation.chain = [{"command": "migrate", "task_id": "task-1", "site": "example.test"}]
    operation.id = "op-1"
    bench.migrations.root = tmp_path
    bench.migrations.get.return_value = operation
    site = MagicMock()
    operation.site.return_value = site
    task_store = MagicMock()
    task_store.read_status.return_value = task_status
    task_process = MagicMock()
    task_process.read.return_value = MagicMock(identity=MagicMock())
    task_process.ownership.return_value = ownership
    task_process._inspector.owned_pids.return_value = []
    return bench, operation, site, task_store, task_process


def test_killed_migrate_transitions_to_needs_attention(tmp_path):
    bench, operation, site, tasks, processes = _bench(tmp_path, TaskStatus.KILLED)
    with patch("pilot.core.bench.migration.recovery.TaskStore", return_value=tasks), patch(
        "pilot.core.bench.migration.recovery.TaskProcess", return_value=processes
    ):
        reconcile_migration(bench, "op-1")
    assert site.migration_status == "failed"
    operation._enter_needs_attention.assert_called_once_with("migrating", "example.test")
    assert operation.diagnosis["phase"] == "migrating"


@pytest.mark.parametrize("status", [TaskStatus.RUNNING, TaskStatus.SUCCESS, TaskStatus.QUEUED])
def test_live_or_successful_task_is_not_reconciled(tmp_path, status):
    bench, operation, _, tasks, processes = _bench(tmp_path, status)
    with patch("pilot.core.bench.migration.recovery.TaskStore", return_value=tasks), patch(
        "pilot.core.bench.migration.recovery.TaskProcess", return_value=processes
    ), pytest.raises(MigrationStateError):
        reconcile_migration(bench, "op-1")
    operation._enter_needs_attention.assert_not_called()


def test_owned_process_is_not_reconciled(tmp_path):
    bench, operation, _, tasks, processes = _bench(
        tmp_path, TaskStatus.KILLED, ProcessOwnership.OWNED
    )
    with patch("pilot.core.bench.migration.recovery.TaskStore", return_value=tasks), patch(
        "pilot.core.bench.migration.recovery.TaskProcess", return_value=processes
    ), pytest.raises(MigrationStateError):
        reconcile_migration(bench, "op-1")
    operation._enter_needs_attention.assert_not_called()
