from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.managers.processes.local import ProcessManager
from pilot.managers.processes.systemd import SystemdProcessManager
from pilot.tasks.update_cli import UpdateCliTask


def make_task(bench_root: Path) -> UpdateCliTask:
    return UpdateCliTask(bench=MagicMock(), bench_root=bench_root)


def run_with(task: UpdateCliTask, manager: MagicMock) -> MagicMock:
    with (
        patch("pilot.updater.perform_upgrade") as upgrade,
        patch("pilot.managers.processes.local.ProcessManager.detect_running", return_value=manager),
    ):
        task.run()
    return upgrade


def test_run_upgrades_then_restarts_admin(tmp_path: Path) -> None:
    manager = MagicMock(spec=SystemdProcessManager)

    upgrade = run_with(make_task(tmp_path), manager)

    upgrade.assert_called_once()
    manager.restart_admin.assert_called_once_with()


def test_run_under_pilot_start_tells_the_operator_to_restart(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    manager = MagicMock(spec=ProcessManager)

    run_with(make_task(tmp_path), manager)

    manager.restart_admin.assert_not_called()
    assert "Restart it to run the new version" in capsys.readouterr().out
