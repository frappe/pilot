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


def test_cron_module_command_pins_pythonpath_to_the_source_root() -> None:
    """Cron starts in the home directory, where the source tree shadows the `pilot` package."""
    from pilot.managers.cron import cron_module_command
    from pilot.utils import cli_root

    command = cron_module_command(
        "pilot.tasks.backup_site", ["/benches/b1", "site.localhost"], Path("/tmp/b.log")
    )

    assert command.startswith(f"PYTHONPATH={cli_root()} ")
    assert " -m pilot.tasks.backup_site /benches/b1 site.localhost " in command
    assert command.endswith(">> /tmp/b.log 2>&1")


def test_cron_module_command_quotes_paths_with_spaces() -> None:
    from pilot.managers.cron import cron_module_command

    command = cron_module_command(
        "pilot.core.registry_cache", [Path("/root dir/cli")], Path("/log dir/out.log")
    )

    assert "'/root dir/cli'" in command
    assert "'/log dir/out.log'" in command


def test_get_schedule_reads_the_expression_ahead_of_the_environment_prefix() -> None:
    """The five cron fields still lead the line once the command carries a PYTHONPATH prefix."""
    manager = make_manager()
    marker = manager._marker("job1")
    entry = "30 14 * * * PYTHONPATH=/home/frappe/pilot /usr/bin/python -m pilot.tasks.backup_site"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _crontab_result(f"{marker}\n{entry}\n")
        assert manager.get_schedule("job1") == "30 14 * * *"
