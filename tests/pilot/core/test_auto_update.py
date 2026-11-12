from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import pilot
from pilot.config import BenchConfig
from pilot.config.common import CommonConfig
from pilot.core.server import Server
from pilot.core.server.auto_update import CRON_JOB_KEY, CRON_SCHEDULE


@pytest.fixture
def benches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("b-bench", "a-bench"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "bench.toml").write_text(BenchConfig.from_flat(name, {}).dumps())
    monkeypatch.setattr("pilot.core.server.Server.benches_dir", tmp_path)
    monkeypatch.setattr(pilot, "is_dev_build", False)
    return tmp_path


def test_auto_update_is_on_by_default_and_not_written(tmp_path: Path) -> None:
    config = CommonConfig()

    assert config.updates.auto_pilot_update is True
    assert "updates" not in config._to_toml_dict()


def test_the_setting_round_trips_through_common_config(tmp_path: Path) -> None:
    with CommonConfig.open(tmp_path) as config:
        config.updates.auto_pilot_update = False

    assert "[updates]\nauto_pilot_update = false" in CommonConfig.path(tmp_path).read_text()
    assert CommonConfig.read(tmp_path).updates.auto_pilot_update is False


def test_turning_it_on_saves_the_setting_and_installs_the_daily_cron(benches: Path) -> None:
    with patch("pilot.core.server.auto_update.CronManager") as cron:
        Server().auto_update.enable()

    assert Server().auto_update.is_enabled
    key, schedule, command = cron.return_value.set_schedule.call_args.args
    assert (key, schedule) == (CRON_JOB_KEY, CRON_SCHEDULE)
    assert "-m pilot.core.server.auto_update" in command


@pytest.mark.parametrize("utc_time", ["0000", "0030", "1200"])
def test_the_cron_guard_runs_only_at_utc_midnight(benches, tmp_path, utc_time, monkeypatch):
    date = tmp_path / "date"
    date.write_text('#!/bin/sh\n[ "$1" = -u ] && [ "$2" = +%H%M ] || exit 1\nprintf "$UTC_TIME"\n')
    date.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    monkeypatch.setenv("UTC_TIME", utc_time)
    with (
        patch("pilot.core.server.auto_update.CronManager") as cron,
        patch("pilot.core.server.auto_update.cron_module_command", return_value="printf ran"),
    ):
        Server().auto_update.enable()
    command = cron.return_value.set_schedule.call_args.args[2]
    # Cron removes the backslash from escaped percent signs before starting the shell.
    result = subprocess.run(["/bin/sh", "-c", command.replace(r"\%", "%")], capture_output=True)
    assert result.stdout == (b"ran" if utc_time == "0000" else b"")


def test_turning_it_off_saves_the_setting_and_removes_the_cron(benches: Path) -> None:
    with patch("pilot.core.server.auto_update.CronManager") as cron:
        Server().auto_update.enable()
        Server().auto_update.disable()

    assert not Server().auto_update.is_enabled
    cron.return_value.remove_schedule.assert_called_once_with(CRON_JOB_KEY)


def test_a_newer_release_queues_one_update_on_the_first_bench(benches: Path) -> None:
    Server().auto_update.set_enabled(True)

    with (
        patch("pilot.updater.update_available", return_value=(True, "v0.0.90")),
        patch("pilot.tasks.update_cli.UpdateCliTask.queue", return_value="task-1") as queue,
    ):
        assert Server().auto_update.run() == "task-1"

    bench = queue.call_args.args[0]
    assert bench.path == benches / "a-bench"
    assert queue.call_args.kwargs == {"idempotency_key": "auto-update:v0.0.90"}


@pytest.mark.parametrize(
    ("enabled", "is_dev_build", "available"),
    [(False, False, True), (True, True, True), (True, False, False)],
    ids=["turned off", "dev checkout", "up to date"],
)
def test_nothing_is_queued_unless_on_a_release_build_with_a_newer_release(
    benches: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool, is_dev_build: bool, available: bool
) -> None:
    Server().auto_update.set_enabled(enabled)
    monkeypatch.setattr(pilot, "is_dev_build", is_dev_build)

    with (
        patch("pilot.updater.update_available", return_value=(available, "v0.0.90")),
        patch("pilot.tasks.update_cli.UpdateCliTask.queue") as queue,
    ):
        assert Server().auto_update.run() is None

    queue.assert_not_called()


@pytest.mark.parametrize("enabled", [True, False])
def test_the_upgrade_patch_reschedules_only_enabled_hosts_once(benches, enabled):
    from pilot.patches.update_auto_update_schedule import run

    Server().auto_update.set_enabled(enabled)
    with patch("pilot.core.server.auto_update.CronManager") as cron:
        run(benches)
        run(benches)
    assert cron.return_value.set_schedule.call_count == int(enabled)
    assert Server().auto_update.is_enabled is enabled


def test_a_failed_schedule_patch_can_retry(benches):
    from pilot.exceptions import CronError
    from pilot.internal.patch_state import is_applied
    from pilot.patches.update_auto_update_schedule import PATCH_NAME, run

    Server().auto_update.set_enabled(True)
    with patch("pilot.core.server.auto_update.CronManager") as cron:
        cron.return_value.set_schedule.side_effect = CronError("Could not write the crontab.")
        with pytest.raises(CronError):
            run(benches)
    assert not is_applied(benches, PATCH_NAME)


def test_the_schedule_patch_skips_source_installs(benches, monkeypatch):
    from pilot.internal.patch_state import is_applied
    from pilot.patches.update_auto_update_schedule import PATCH_NAME, run

    monkeypatch.setattr(pilot, "is_dev_build", True)
    with patch("pilot.core.server.auto_update.CronManager") as cron:
        run(benches)
    cron.assert_not_called()
    assert not is_applied(benches, PATCH_NAME)


def test_a_fresh_release_install_enables_updates_without_a_bench(tmp_path, monkeypatch):
    from pilot.patches.update_auto_update_schedule import run

    root = tmp_path / "new-install" / "benches"
    monkeypatch.setattr("pilot.core.server.Server.benches_dir", root)
    monkeypatch.setattr(pilot, "is_dev_build", False)
    with patch("pilot.core.server.auto_update.CronManager") as cron:
        run(root)
    cron.return_value.set_schedule.assert_called_once()
    assert Server().auto_update.is_enabled
