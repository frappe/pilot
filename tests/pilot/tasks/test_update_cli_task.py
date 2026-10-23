from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.managers.processes.local import ProcessManager
from pilot.managers.processes.systemd import SystemdProcessManager
from pilot.tasks.update_cli import UpdateCliTask
from pilot.updater import restart_admin


def restart_with(manager: MagicMock, bench_path: Path) -> None:
    with (
        patch("pilot.core.bench.Bench"),
        patch("pilot.managers.processes.local.ProcessManager.detect_running", return_value=manager),
    ):
        restart_admin(bench_path)


def test_run_upgrades_then_restarts_admin_in_a_new_process(tmp_path: Path) -> None:
    task = UpdateCliTask(bench=MagicMock(path=tmp_path), bench_root=tmp_path)

    with (
        patch("pilot.updater.perform_upgrade") as upgrade,
        patch("pilot.updater.subprocess.run") as run,
    ):
        task.run()

    upgrade.assert_called_once()
    assert run.call_args.args[0][1:] == ["-m", "pilot.updater", str(tmp_path)]


def test_restart_admin_restarts_a_managed_admin(tmp_path: Path) -> None:
    manager = MagicMock(spec=SystemdProcessManager)

    restart_with(manager, tmp_path)

    manager.restart_admin.assert_called_once_with()


def test_restart_admin_under_pilot_start_tells_the_operator_to_restart(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    manager = MagicMock(spec=ProcessManager)

    restart_with(manager, tmp_path)

    manager.restart_admin.assert_not_called()
    assert "Restart it to run the new version" in capsys.readouterr().out
