from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.core.bench.production import BenchProduction
from pilot.core.bench.settings import BenchSettings, restart_trigger_values
from pilot.exceptions import CommandError
from tests.pilot.managers.test_managers_extra import make_bench


def test_a_failed_rebuild_still_starts_the_workload(tmp_path: Path) -> None:
    """Stopping the processes first must never leave the sites down."""
    bench = make_bench(tmp_path)
    bench.config.production.enabled = True
    manager = MagicMock()

    with (
        patch("pilot.managers.processes.local.ProcessManager.for_bench", return_value=manager),
        patch.object(type(bench), "write_common_site_config"),
        patch("pilot.core.bench.settings.regenerate_nginx", side_effect=CommandError("nginx -t failed")),
        pytest.raises(CommandError),
    ):
        BenchProduction(bench).rebuild_process_set(on_progress=lambda message: None)

    manager.stop.assert_called_once()
    manager.start_workload.assert_called_once()


def test_an_unsupported_lite_flag_change_restarts_nothing(tmp_path: Path) -> None:
    # Without frappe/runner.py the bench runs the classic process set either way.
    bench = make_bench(tmp_path)
    bench.config.lite_mode.enabled = True
    old_restart = restart_trigger_values(bench.config)
    bench.config.lite_mode.enabled = False

    with (
        patch("pilot.tasks.switch_lite_mode.SwitchLiteModeTask.queue") as queue,
        patch("pilot.core.bench.settings.restart_running_workload") as restart,
    ):
        assert BenchSettings(bench)._regenerate_and_restart_if_needed(old_restart) is False

    queue.assert_not_called()
    restart.assert_not_called()
