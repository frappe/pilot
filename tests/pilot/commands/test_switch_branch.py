"""Tests for SwitchBranchCommand.run()."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.commands.apps.switch_branch import SwitchBranchCommand
from pilot.core.bench import Bench
from pilot.tasks.switch_branch import SwitchBranchTask
from tests.pilot.commands.test_commands import make_bench


def test_run_drives_the_task_inline_and_reloads_workers(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    cmd = SwitchBranchCommand(bench, app_name="erpnext", branch="version-16-hotfix")

    seen = {}

    def record_task(task):
        seen["name"] = task.name
        seen["branch"] = task.branch

    with (
        patch.object(SwitchBranchTask, "run", autospec=True, side_effect=record_task),
        patch.object(Bench, "reload_workers") as mock_reload,
    ):
        cmd.run()

    assert seen == {"name": "erpnext", "branch": "version-16-hotfix"}
    mock_reload.assert_called_once()


def test_run_skips_worker_reload_when_the_switch_fails(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    cmd = SwitchBranchCommand(bench, app_name="erpnext", branch="gone")

    with (
        patch.object(SwitchBranchTask, "run", side_effect=SystemExit(1)),
        patch.object(Bench, "reload_workers") as mock_reload,
        pytest.raises(SystemExit),
    ):
        cmd.run()

    mock_reload.assert_not_called()
