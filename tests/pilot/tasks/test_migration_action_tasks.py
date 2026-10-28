from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pilot.tasks.bypass_patch import BypassPatchTask
from pilot.tasks.retry_update import RetryUpdateTask
from pilot.tasks.revert_migration import RevertMigrationTask


@pytest.mark.parametrize(
    ("task_class", "extra"),
    [(RetryUpdateTask, {}), (RevertMigrationTask, {}), (BypassPatchTask, {"patch": "app.patches.one"})],
)
def test_an_action_hands_its_locks_to_the_chain_from_its_own_id(
    tmp_path: Path, monkeypatch, task_class, extra
) -> None:
    """The API records the action's id only after it queued the task, so the task cannot read it from there."""
    bench = MagicMock()
    operation = bench.migrations.get.return_value
    operation.task_ids = {}
    monkeypatch.setenv("BENCH_TASK_ID", "20261027-000000-aaaaaa")

    task_class(bench=bench, bench_root=tmp_path, operation_id="op1", **extra).run()

    operation.enqueue_next.assert_called_once_with(handoff_from="20261027-000000-aaaaaa")
