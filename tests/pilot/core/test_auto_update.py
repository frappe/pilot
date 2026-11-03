from __future__ import annotations

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
    monkeypatch.setattr("pilot.utils.benches_dir", lambda: tmp_path)
    monkeypatch.setattr(pilot, "is_dev_build", False)
    return tmp_path


def test_auto_update_is_off_by_default_and_not_written(tmp_path: Path) -> None:
    config = CommonConfig()

    assert config.updates.auto is False
    assert "updates" not in config._to_toml_dict()


def test_the_setting_round_trips_through_common_config(tmp_path: Path) -> None:
    with CommonConfig.open(tmp_path) as config:
        config.updates.auto = True

    assert "[updates]\nauto = true" in CommonConfig.path(tmp_path).read_text()
    assert CommonConfig.from_raw_dict({"updates": {"auto": True}}, strict=True).updates.auto is True


def test_turning_it_on_saves_the_setting_and_installs_the_daily_cron(benches: Path) -> None:
    with patch("pilot.core.server.auto_update.CronManager") as cron:
        Server().auto_update.enable()

    assert Server().auto_update.is_enabled
    key, schedule, command = cron.return_value.set_schedule.call_args.args
    assert (key, schedule) == (CRON_JOB_KEY, CRON_SCHEDULE)
    assert "-m pilot.core.server.auto_update" in command


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
