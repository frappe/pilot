from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.exceptions import BenchError
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


def _benches(tmp_path: Path, *names: str) -> list[Path]:
    paths = [tmp_path / name for name in names]
    for path in paths:
        path.mkdir()
        (path / "bench.toml").write_text("")
    return paths


def test_run_upgrades_then_restarts_every_bench_admin_in_a_new_process(tmp_path: Path) -> None:
    first, second = _benches(tmp_path, "a", "b")
    task = UpdateCliTask(bench=MagicMock(path=first), bench_root=first)

    with (
        patch("pilot.core.server.Server.benches_dir", tmp_path),
        patch("pilot.updater.perform_upgrade") as upgrade,
        patch("pilot.updater.subprocess.run") as run,
    ):
        task.run()

    upgrade.assert_called_once()
    assert [call.args[0][1:] for call in run.call_args_list] == [
        ["-m", "pilot.updater", str(first)],
        ["-m", "pilot.updater", str(second)],
    ]


def test_one_failed_admin_restart_does_not_skip_the_others(tmp_path: Path) -> None:
    first, _ = _benches(tmp_path, "a", "b")
    task = UpdateCliTask(bench=MagicMock(path=first), bench_root=first)
    failure = subprocess.CalledProcessError(1, ["python"])

    with (
        patch("pilot.core.server.Server.benches_dir", tmp_path),
        patch("pilot.updater.subprocess.run", side_effect=[failure, None]) as run,
        pytest.raises(BenchError, match="Could not restart the admin of: a"),
    ):
        task.restart_admins()

    assert run.call_count == 2


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
