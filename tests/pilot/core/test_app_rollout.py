from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.core.app import RevisionPin
from pilot.core.app.repository import AppRepository
from pilot.core.bench.rollout import AppRollout
from pilot.core.server import Server
from pilot.exceptions import BenchError
from pilot.integrations.central import CentralClientError

STEPS = {"start_percent": 5, "step_percent": 5, "interval_hours": 8, "max_percent": 100}


@pytest.fixture(autouse=True)
def cli_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("pilot.utils.cli_root", lambda: tmp_path)
    return tmp_path


def _rollout(tmp_path: Path) -> AppRollout:
    rollout = AppRollout(MagicMock(path=tmp_path, logs_path=tmp_path))
    rollout.settings = STEPS
    return rollout


def _settings_from_central(tmp_path: Path, reply: object, central_enabled: bool = True) -> dict | None:
    bench = MagicMock(path=tmp_path)
    bench.config.central.enabled = central_enabled
    with patch("pilot.integrations.central.client.CentralClient.config", return_value=reply):
        return AppRollout(bench).settings


def _set_rollout_number(cli_root: Path, number: int) -> None:
    (cli_root / "system").mkdir(exist_ok=True)
    (cli_root / "system" / "rollout-number").write_text(str(number))


def _added_hours_ago(hours: int):
    added_at = datetime.now(UTC) - timedelta(hours=hours)
    return patch("pilot.core.registry_cache.RegistryCache.get_added_at", return_value=added_at)


def test_percent_grows_one_step_per_full_interval_up_to_100(tmp_path: Path) -> None:
    rollout = _rollout(tmp_path)
    now = datetime.now(UTC)

    assert rollout.get_percent(now) == 5
    assert rollout.get_percent(now - timedelta(hours=7)) == 5
    assert rollout.get_percent(now - timedelta(hours=8)) == 10
    assert rollout.get_percent(now - timedelta(hours=1000)) == 100
    assert rollout.get_percent(now + timedelta(hours=20)) == 5


def test_the_max_percent_caps_the_rollout(tmp_path: Path) -> None:
    rollout = _rollout(tmp_path)
    rollout.settings = {**STEPS, "max_percent": 30}

    assert rollout.get_percent(datetime.now(UTC) - timedelta(hours=1000)) == 30


def test_a_max_percent_of_0_offers_the_update_to_no_server(tmp_path: Path, cli_root: Path) -> None:
    _set_rollout_number(cli_root, 1)
    rollout = _rollout(tmp_path)
    rollout.settings = {**STEPS, "max_percent": 0}

    with _added_hours_ago(1000):
        assert rollout.is_offered("frappe", "abc") is False


def test_a_max_percent_of_0_is_valid(tmp_path: Path) -> None:
    steps = {**STEPS, "max_percent": 0}

    assert _settings_from_central(tmp_path, {"app_rollout": steps}) == steps


def test_the_steps_come_from_central(tmp_path: Path) -> None:
    assert _settings_from_central(tmp_path, {"app_rollout": STEPS}) == STEPS


@pytest.mark.parametrize("reply", [{}, {"app_rollout": None}])
def test_a_central_without_rollout_steps_means_no_rollout(tmp_path: Path, reply: dict) -> None:
    assert _settings_from_central(tmp_path, reply) is None


@pytest.mark.parametrize(
    "steps",
    [
        {**STEPS, "step_percent": 0},
        {**STEPS, "interval_hours": "8"},
        {**STEPS, "interval_hours": True},
        {**STEPS, "start_percent": 101},
        {**STEPS, "step_percent": 101},
        {**STEPS, "max_percent": -1},
        {**STEPS, "max_percent": 101},
        {**STEPS, "max_percent": True},
        {"start_percent": 5, "step_percent": 5, "interval_hours": 8},
        {"start_percent": 5},
        "5,5,8",
    ],
)
def test_bad_steps_make_updates_wait(tmp_path: Path, steps: object) -> None:
    assert _settings_from_central(tmp_path, {"app_rollout": steps}) is False


@pytest.mark.parametrize("reply", [None, ["app_rollout"]])
def test_a_reply_that_is_not_an_object_makes_updates_wait(tmp_path: Path, reply: object) -> None:
    assert _settings_from_central(tmp_path, reply) is False


def test_every_update_is_offered_without_rollout_steps(tmp_path: Path, cli_root: Path) -> None:
    _set_rollout_number(cli_root, 100)
    rollout = AppRollout(MagicMock(path=tmp_path))
    rollout.settings = None

    assert rollout.is_offered("frappe", "abc") is True


def test_a_bench_without_central_asks_central_nothing(tmp_path: Path) -> None:
    with patch("pilot.integrations.central.client.CentralClient.config") as config:
        assert _settings_from_central(tmp_path, {"app_rollout": STEPS}, central_enabled=False) is None

    config.assert_not_called()


def test_updates_wait_while_central_is_unreachable(tmp_path: Path, cli_root: Path) -> None:
    _set_rollout_number(cli_root, 1)
    bench = MagicMock(path=tmp_path)
    with patch(
        "pilot.integrations.central.client.CentralClient.config", side_effect=CentralClientError("down")
    ):
        assert AppRollout(bench).is_offered("frappe", "abc") is False


def test_an_update_reaches_a_server_once_the_percent_passes_its_number(
    tmp_path: Path, cli_root: Path
) -> None:
    _set_rollout_number(cli_root, 37)
    rollout = _rollout(tmp_path)

    with _added_hours_ago(0):
        assert rollout.is_offered("frappe", "abc") is False

    with _added_hours_ago(7 * 8):
        assert rollout.is_offered("frappe", "abc") is True


def test_an_update_waits_when_the_registry_has_no_time_for_it(tmp_path: Path, cli_root: Path) -> None:
    _set_rollout_number(cli_root, 1)
    rollout = _rollout(tmp_path)

    with patch("pilot.core.registry_cache.RegistryCache.get_added_at", return_value=None):
        assert rollout.is_offered("frappe", "abc") is False


def test_a_held_commit_is_not_offered_even_at_100_percent(tmp_path: Path, cli_root: Path) -> None:
    _set_rollout_number(cli_root, 1)
    rollout = _rollout(tmp_path)
    rollout.settings = {**STEPS, "held_commits": ["bad"]}

    with _added_hours_ago(1000):
        assert rollout.is_offered("frappe", "bad") is False
        assert rollout.is_offered("frappe", "good") is True


def test_a_central_without_held_commits_holds_nothing(tmp_path: Path) -> None:
    assert _settings_from_central(tmp_path, {"app_rollout": STEPS}) == STEPS


@pytest.mark.parametrize("held_commits", ["abc", [1], [None]])
def test_bad_held_commits_make_updates_wait(tmp_path: Path, held_commits: object) -> None:
    steps = {**STEPS, "held_commits": held_commits}
    assert _settings_from_central(tmp_path, {"app_rollout": steps}) is False


def test_the_rollout_number_is_chosen_once(cli_root: Path) -> None:
    number = Server().get_rollout_number()

    assert 1 <= number <= 100
    assert Server().get_rollout_number() == number


@pytest.mark.parametrize("content", ["not a number", "0", "101", "-5"])
def test_a_damaged_rollout_number_fails_loudly_and_is_kept(cli_root: Path, content: str) -> None:
    path = cli_root / "system" / "rollout-number"
    path.parent.mkdir()
    path.write_text(content)

    with pytest.raises(BenchError, match="is damaged"):
        Server().get_rollout_number()

    assert path.read_text() == content


def test_a_damaged_rollout_number_does_not_fail_the_update_log(
    tmp_path: Path, cli_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_rollout_number(cli_root, 500)

    AppRollout(MagicMock(logs_path=tmp_path)).log_updates([], "success")

    assert "Could not write the app update log" in capsys.readouterr().out


def _repository(releases: list[dict], offered: set[str], installed: str = "") -> AppRepository:
    app = MagicMock()
    app.config.name = "frappe"
    app.config.branch = "main"
    app.config.follow_branch = False
    app.bench.app_rollout.is_offered.side_effect = lambda _app, commit: commit in offered
    repository = AppRepository(app)
    repository.get_branch_releases = MagicMock(return_value=releases)
    repository._is_ahead_of_installed = MagicMock(side_effect=lambda commit, _version: commit != installed)
    return repository


ENTRY = {"name": "frappe"}
RELEASE = {"version": "16.1.0", "branch": "main", "commit": "abc"}
OLDER_RELEASE = {"version": "16.0.0", "branch": "main", "commit": "old"}


def test_update_target_holds_back_a_release_the_rollout_has_not_offered() -> None:
    repository = _repository([RELEASE], offered=set())

    with patch.object(AppRepository, "marketplace_entry", ENTRY):
        assert repository.update_target() is None

    repository.app.bench.app_rollout.is_offered.assert_called_once_with("frappe", "abc")


def test_update_target_gives_a_release_the_rollout_has_offered() -> None:
    repository = _repository([RELEASE], offered={"abc"})

    with patch.object(AppRepository, "marketplace_entry", ENTRY):
        assert repository.update_target() == RevisionPin(kind="commit", ref="abc")


def test_update_target_gives_an_older_release_while_the_newest_has_not_reached_this_server() -> None:
    repository = _repository([RELEASE, OLDER_RELEASE], offered={"old"})

    with patch.object(AppRepository, "marketplace_entry", ENTRY):
        assert repository.update_target() == RevisionPin(kind="commit", ref="old")


def test_update_target_stops_at_the_newest_offered_release_when_it_is_installed() -> None:
    repository = _repository([RELEASE, OLDER_RELEASE], offered={"abc", "old"}, installed="abc")

    with patch.object(AppRepository, "marketplace_entry", ENTRY):
        assert repository.update_target() is None

    repository.app.bench.app_rollout.is_offered.assert_called_once_with("frappe", "abc")


def test_update_target_gives_nothing_when_only_the_installed_release_is_offered() -> None:
    repository = _repository([RELEASE, OLDER_RELEASE], offered={"old"}, installed="old")

    with patch.object(AppRepository, "marketplace_entry", ENTRY):
        assert repository.update_target() is None


def test_update_target_gives_a_branch_app_its_new_commit_at_once() -> None:
    repository = _repository([], offered=set())
    branch_repo = MagicMock()
    branch_repo.remote_branch_sha.return_value = "tip"

    with (
        patch.object(AppRepository, "marketplace_entry", None),
        patch.object(AppRepository, "repo", branch_repo),
    ):
        assert repository.update_target() == RevisionPin(kind="commit", ref="tip")

    repository.app.bench.app_rollout.is_offered.assert_not_called()
