"""Tests for SwitchBranchTask validating the branch it switches to."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import call, patch

import pytest

from pilot.core.app import App
from pilot.exceptions import AppValidationError, BenchError, CommandError
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


@pytest.mark.parametrize(
    ("name", "branch", "message"),
    [("../myapp", "develop", "App name"), ("myapp", "../develop", "Branch name")],
)
def test_switch_branch_rejects_unsafe_arguments(
    tmp_path: Path, name: str, branch: str, message: str
) -> None:
    bench = make_bench(tmp_path)
    task = SwitchBranchTask(bench=bench, bench_root=bench.path, name=name, branch=branch)

    with pytest.raises(BenchError, match=message):
        task.run()


def test_switch_branch_installs_a_branch_that_validates(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, '[{"doctype": "Role"}]\n')

    with (
        patch.object(App, "head_sha", "abc1234"),
        patch.object(App, "current_branch", "main"),
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
        patch.object(App, "current_branch", "main"),
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
        patch.object(App, "current_branch", "main"),
        patch.object(App, "switch_branch") as mock_switch,
        patch.object(App, "validate", side_effect=CommandError("uv exploded")),
        patch.object(PythonEnvManager, "install_app") as mock_install,
        pytest.raises(CommandError),
    ):
        _task(bench).run()

    assert mock_switch.call_args_list == [call("develop"), call("main")]
    mock_install.assert_not_called()
