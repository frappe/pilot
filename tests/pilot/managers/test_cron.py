"""Tests for pilot.managers.cron.CronManager."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from pilot.managers.cron import CronManager


def make_manager(bench_root: str = "/benches/test") -> CronManager:
    return CronManager(Path(bench_root))


def _crontab_result(stdout: str, returncode: int = 0) -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    return result


def test_set_schedule_appends_new_marker_and_entry() -> None:
    manager = make_manager()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _crontab_result("")
        manager.set_schedule("job1", "0 3 * * *", "run-it")

    write_call = mock_run.call_args_list[-1]
    written = write_call.kwargs["input"]
    assert manager._marker("job1") in written
    assert "0 3 * * * run-it" in written


def test_set_schedule_updates_existing_entry() -> None:
    manager = make_manager()
    marker = manager._marker("job1")
    existing = f"{marker}\n0 1 * * * old-command\n"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _crontab_result(existing)
        manager.set_schedule("job1", "0 3 * * *", "new-command")

    written = mock_run.call_args_list[-1].kwargs["input"]
    assert "0 3 * * * new-command" in written
    assert "old-command" not in written


def test_set_schedule_does_not_raise_when_marker_is_last_line() -> None:
    """Regression: a truncated/hand-edited crontab where the marker exists
    but its job line was cut off must not crash with IndexError."""
    manager = make_manager()
    marker = manager._marker("job1")
    existing = f"{marker}\n"  # marker present, no line after it

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _crontab_result(existing)
        manager.set_schedule("job1", "0 3 * * *", "run-it")  # must not raise

    written = mock_run.call_args_list[-1].kwargs["input"]
    assert marker in written
    assert "0 3 * * * run-it" in written


def test_remove_schedule_deletes_marker_and_entry() -> None:
    manager = make_manager()
    marker = manager._marker("job1")
    existing = f"{marker}\n0 3 * * * run-it\n"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _crontab_result(existing)
        manager.remove_schedule("job1")

    write_call = mock_run.call_args_list[-1]
    assert write_call.args[0] == ["crontab", "-r"]
