from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from pilot.tasks.update_cli import UpdateCliTask


def make_task(bench_root: Path) -> UpdateCliTask:
    return UpdateCliTask(bench=MagicMock(), bench_root=bench_root)


def test_run_upgrades_then_restarts_admin(tmp_path: Path) -> None:
    task = make_task(tmp_path)
    manager = MagicMock()
    with (
        patch("pilot.updater.perform_upgrade") as upgrade,
        patch(
            "pilot.managers.processes.local.ProcessManager.detect_running",
            return_value=manager,
        ),
    ):
        task.run()

    upgrade.assert_called_once()
    manager.restart_admin.assert_called_once_with()
