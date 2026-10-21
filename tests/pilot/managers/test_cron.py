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


def test_cron_module_command_pins_pythonpath_to_the_source_root(tmp_path: Path) -> None:
    """Cron starts in the home directory, where the source tree shadows the `pilot` package."""
    from pilot.managers.cron import cron_module_command
    from pilot.utils import cli_root

    admin_python = tmp_path / "bin" / "python"
    admin_python.parent.mkdir()
    admin_python.touch()
    with patch("pilot.managers.environment.AdminEnvManager.python", new=admin_python):
        command = cron_module_command(
            "pilot.tasks.backup_site", ["/benches/b1", "site.localhost"], Path("/tmp/b.log")
        )

    assert command.startswith(f"PYTHONPATH={cli_root()} {admin_python} ")
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


def test_missing_crontab_binary_names_the_package_to_install() -> None:
    import pytest

    from pilot.exceptions import CronError

    with patch("shutil.which", return_value=None), pytest.raises(CronError, match="Install cron"):
        make_manager().get_schedule("job1")


def test_unreadable_crontab_is_not_treated_as_empty() -> None:
    """Writing after a failed read would wipe the user's other cron entries."""
    import pytest

    from pilot.exceptions import CronError

    failed = _crontab_result("", returncode=1)
    failed.stderr = "crontab: cannot connect to the cron daemon"
    with (
        patch("shutil.which", return_value="/usr/bin/crontab"),
        patch("subprocess.run", return_value=failed) as run,
        pytest.raises(CronError, match="Could not read the crontab"),
    ):
        make_manager().set_schedule("job1", "0 3 * * *", "run-it")

    assert run.call_count == 1


def test_no_crontab_yet_reads_as_empty() -> None:
    empty = _crontab_result("", returncode=1)
    empty.stderr = "no crontab for frappe"
    with patch("shutil.which", return_value="/usr/bin/crontab"), patch("subprocess.run", return_value=empty):
        assert make_manager().get_schedule("job1") is None


def test_remove_schedule_leaves_the_crontab_alone_without_the_entry() -> None:
    manager = make_manager()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _crontab_result("0 */6 * * * someone-elses-job\n\n# their comment\n")
        manager.remove_schedule("job1")

    assert [call.args[0] for call in mock_run.call_args_list] == [["crontab", "-l"]]


def test_move_schedule_rekeys_the_entry_and_keeps_its_schedule() -> None:
    manager = make_manager()
    existing = f"0 */6 * * * someone-elses-job\n{manager._marker('old')}\n0 3 * * * run-old\n"

    with patch("shutil.which", return_value="/usr/bin/crontab"), patch("subprocess.run") as mock_run:
        mock_run.return_value = _crontab_result(existing)
        manager.move_schedule("old", "new", "run-new")

    written = mock_run.call_args_list[-1].kwargs["input"].splitlines()
    assert written == ["0 */6 * * * someone-elses-job", manager._marker("new"), "0 3 * * * run-new"]


def test_set_schedule_does_not_rewrite_an_unchanged_entry() -> None:
    manager = make_manager()
    existing = f"{manager._marker('job1')}\n0 3 * * * run-it\n"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = _crontab_result(existing)
        manager.set_schedule("job1", "0 3 * * *", "run-it")

    assert all(call.kwargs.get("input") is None for call in mock_run.call_args_list)
