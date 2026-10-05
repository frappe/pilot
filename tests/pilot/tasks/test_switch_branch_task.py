"""Tests for SwitchBranchTask validating the branch it switches to."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import call, patch

import pytest

from pilot.core.app import App
from pilot.exceptions import AppValidationError, CommandError
from pilot.managers.environment import PythonEnvManager
from pilot.tasks.switch_branch import SwitchBranchTask
from tests.pilot.commands.test_commands import make_bench


def _write_app(bench, fixture: str) -> None:
    app_path = bench.apps_path / "myapp"
    (app_path / "myapp").mkdir(parents=True)
    (app_path / ".git").mkdir()
    (app_path / "pyproject.toml").write_text(
        '[project]\nname = "myapp"\n\n[tool.bench.frappe-dependencies]\nfrappe = ">=16.0.0,<17.0.0"\n'
    )
    (app_path / "myapp" / "__init__.py").write_text("")
    (app_path / "myapp" / "hooks.py").write_text("app_name = 'myapp'\n")
    (app_path / "myapp" / "fixtures").mkdir()
    (app_path / "myapp" / "fixtures" / "role.json").write_text(fixture)


def _task(bench) -> SwitchBranchTask:
    return SwitchBranchTask(bench=bench, bench_root=bench.path, name="myapp", branch="develop")


def test_switch_branch_installs_a_branch_that_validates(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, '[{"doctype": "Role"}]\n')

    with (
        patch.object(App, "head_sha", "abc1234"),
        patch("pilot.core.bench.inventory.BenchInventory._git_branch", return_value="main"),
        patch.object(App, "checkout_commit"),
        patch.object(App, "switch_branch"),
        patch.object(App, "record_branch"),
        patch.object(PythonEnvManager, "install_app") as mock_install,
        patch.object(PythonEnvManager, "build_assets_for_app") as mock_build,
    ):
        _task(bench).run()

    mock_install.assert_called_once()
    mock_build.assert_called_once()


def test_switch_branch_returns_to_the_old_branch_when_the_new_one_is_broken(tmp_path: Path) -> None:
    """Back to the branch, not its commit - a detached HEAD would disagree with
    the branch bench.toml records."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, "{not json\n")

    with (
        patch.object(App, "head_sha", "abc1234"),
        patch("pilot.core.bench.inventory.BenchInventory._git_branch", return_value="main"),
        patch.object(App, "checkout_commit"),
        patch.object(App, "switch_branch") as mock_switch,
        patch.object(PythonEnvManager, "install_app") as mock_install,
        pytest.raises(AppValidationError, match=r"fixtures/role\.json"),
    ):
        _task(bench).run()

    assert mock_switch.call_args_list == [call("develop"), call("main")]
    mock_install.assert_not_called()


def test_switch_branch_returns_to_the_old_commit_when_head_was_detached(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, "{not json\n")

    with (
        patch.object(App, "head_sha", "abc1234"),
        patch.object(App, "current_branch", ""),
        patch.object(App, "switch_branch"),
        patch.object(App, "checkout_commit") as mock_checkout,
        pytest.raises(AppValidationError, match=r"fixtures/role\.json"),
    ):
        _task(bench).run()

    mock_checkout.assert_called_once_with("abc1234")


def test_switch_branch_rolls_back_when_a_check_itself_fails(tmp_path: Path) -> None:
    """A check can die on its own tooling - uv falling over leaves the same live
    bad branch as a validation failure does."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, '[{"doctype": "Role"}]\n')

    with (
        patch.object(App, "head_sha", "abc1234"),
        patch("pilot.core.bench.inventory.BenchInventory._git_branch", return_value="main"),
        patch.object(App, "checkout_commit"),
        patch.object(App, "switch_branch") as mock_switch,
        patch.object(App, "validate", side_effect=CommandError("uv exploded")),
        patch.object(PythonEnvManager, "install_app") as mock_install,
        pytest.raises(CommandError),
    ):
        _task(bench).run()

    assert mock_switch.call_args_list == [call("develop"), call("main")]
    mock_install.assert_not_called()


def test_a_failed_reinstall_returns_to_the_old_branch_and_reinstalls_it(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, '[{"doctype": "Role"}]\n')

    with (
        patch.object(App, "head_sha", "abc1234"),
        patch("pilot.core.bench.inventory.BenchInventory._git_branch", return_value="main"),
        patch.object(App, "checkout_commit"),
        patch.object(App, "switch_branch") as mock_switch,
        patch.object(App, "record_branch") as mock_record,
        patch.object(PythonEnvManager, "install_app", side_effect=[CommandError("uv failed"), None]),
        patch.object(PythonEnvManager, "build_assets_for_app") as mock_build,
        pytest.raises(CommandError),
    ):
        _task(bench).run()

    assert mock_switch.call_args_list == [call("develop"), call("main")]
    mock_build.assert_called_once()  # the old branch's assets, rebuilt
    mock_record.assert_not_called()


def test_sites_with_the_app_are_migrated_under_the_switch_locks(tmp_path: Path, monkeypatch) -> None:
    """The chain takes over this task's locks, so an update cannot start in between."""
    bench = make_bench(tmp_path)
    operations = []

    class FakeOperation:
        id = "op1"

        def begin(self, handoff_from=None):
            operations.append(handoff_from)

    monkeypatch.setenv("BENCH_TASK_ID", "20261004-000000-aaaaaa")
    with patch.object(type(bench.migrations), "create_site_migrate", return_value=FakeOperation()) as create:
        SwitchBranchTask(
            bench=bench, bench_root=bench.path, name="myapp", branch="develop", sites=["a.localhost"]
        ).queue_site_migrations("main", "1111111", "2222222")

    assert create.call_args.args == ("a.localhost",)
    switched = create.call_args.kwargs["switched_app"]
    assert (switched.name, switched.sha, switched.branch, switched.updated_sha) == ("myapp", "1111111", "main", "2222222")
    assert operations == ["20261004-000000-aaaaaa"]


def test_a_switch_without_the_site_locks_stops_before_changing_code(tmp_path: Path, monkeypatch) -> None:
    """The handed-off migration needs them; finding out at the end leaves sites unmigrated."""
    import json

    from pilot.exceptions import BenchError

    bench = make_bench(tmp_path)
    task_dir = bench.path / "tasks" / "20261004-000000-aaaaaa"
    task_dir.mkdir(parents=True)
    (task_dir / "meta.json").write_text(json.dumps({"resource_keys": ["bench:update"]}))
    monkeypatch.setenv("BENCH_TASK_ID", "20261004-000000-aaaaaa")
    task = SwitchBranchTask(bench=bench, bench_root=bench.path, name="myapp", branch="develop", sites=["a.localhost"])

    with patch.object(App, "switch_branch") as switch, pytest.raises(BenchError, match="queue_switch"):
        task.run()

    switch.assert_not_called()


@pytest.mark.parametrize(("branch", "switched_to", "tracked"), [("main", "main", "main"), ("", None, "")])
def test_return_to_restores_the_commit_on_its_tracked_branch(tmp_path: Path, branch, switched_to, tracked) -> None:
    """A pinned commit stays on its tracked branch; without one the checkout stays detached."""
    from pilot.config import AppConfig

    app = App(AppConfig(name="myapp", repo="https://github.com/frappe/myapp", branch="develop"), make_bench(tmp_path))
    with patch.object(App, "switch_branch") as switch, patch.object(App, "checkout_commit") as checkout:
        app.return_to(branch, "abc1234")

    assert switch.call_args_list == ([call(switched_to)] if switched_to else [])
    checkout.assert_called_once_with("abc1234")
    assert app.config.branch == ("develop" if switched_to else tracked)
