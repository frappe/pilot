"""Tests for SwitchBranchTask validating the branch it switches to."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import ANY, patch

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
        patch.object(App, "current_branch", "main"),
        patch.object(App, "switch_branch"),
        patch.object(App, "record_branch"),
        patch.object(PythonEnvManager, "install_app") as mock_install,
        patch.object(PythonEnvManager, "build_assets_for_app") as mock_build,
    ):
        _task(bench).run()

    mock_install.assert_called_once()
    mock_build.assert_called_once()


def test_switch_branch_returns_to_the_exact_old_branch_when_the_new_one_is_broken(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, "{not json\n")

    with (
        patch.object(App, "head_sha", "abc1234"),
        patch.object(App, "current_branch", "main"),
        patch.object(App, "switch_branch") as mock_switch,
        patch.object(App, "restore_revision") as mock_restore,
        patch.object(App, "record_branch"),
        patch.object(PythonEnvManager, "install_app"),
        patch.object(PythonEnvManager, "build_assets_for_app"),
        pytest.raises(AppValidationError, match=r"fixtures/role\.json"),
    ):
        _task(bench).run()

    mock_switch.assert_called_once_with("develop", force=False)
    mock_restore.assert_called_once_with("main", "abc1234", ANY)


def test_switch_branch_returns_to_the_exact_old_commit_when_head_was_detached(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, "{not json\n")

    with (
        patch.object(App, "head_sha", "abc1234"),
        patch.object(App, "current_branch", ""),
        patch.object(App, "switch_branch"),
        patch.object(App, "restore_revision") as mock_restore,
        patch.object(App, "record_branch"),
        patch.object(PythonEnvManager, "install_app"),
        patch.object(PythonEnvManager, "build_assets_for_app"),
        pytest.raises(AppValidationError, match=r"fixtures/role\.json"),
    ):
        _task(bench).run()

    mock_restore.assert_called_once_with("", "abc1234", ANY)


def test_switch_branch_rolls_back_when_a_check_itself_fails(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, '[{"doctype": "Role"}]\n')

    with (
        patch.object(App, "head_sha", "abc1234"),
        patch.object(App, "current_branch", "main"),
        patch.object(App, "switch_branch"),
        patch.object(App, "validate", side_effect=CommandError("uv exploded")),
        patch.object(App, "restore_revision") as mock_restore,
        patch.object(App, "record_branch"),
        patch.object(PythonEnvManager, "install_app"),
        patch.object(PythonEnvManager, "build_assets_for_app"),
        pytest.raises(CommandError),
    ):
        _task(bench).run()

    mock_restore.assert_called_once_with("main", "abc1234", ANY)


def test_same_commit_failure_still_rolls_back_after_completed_checkout(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, '[{"doctype": "Role"}]\n')

    with (
        patch.object(App, "head_sha", "abc1234"),
        patch.object(App, "current_branch", "main"),
        patch.object(App, "switch_branch"),
        patch.object(App, "validate"),
        patch.object(App, "restore_revision") as mock_restore,
        patch.object(App, "record_branch"),
        patch.object(PythonEnvManager, "install_app", side_effect=[CommandError("install failed"), None]),
        patch.object(PythonEnvManager, "build_assets_for_app"),
        pytest.raises(CommandError, match="install failed"),
    ):
        _task(bench).run()

    mock_restore.assert_called_once_with("main", "abc1234", ANY)


def test_switch_branch_passes_force_to_checkout(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, '[{"doctype": "Role"}]\n')
    task = SwitchBranchTask(
        bench=bench,
        bench_root=bench.path,
        name="myapp",
        branch="develop",
        force=True,
    )

    with (
        patch.object(App, "head_sha", "abc1234"),
        patch.object(App, "current_branch", "main"),
        patch.object(App, "switch_branch") as mock_switch,
        patch.object(App, "record_branch"),
        patch.object(PythonEnvManager, "install_app"),
        patch.object(PythonEnvManager, "build_assets_for_app"),
    ):
        task.run()

    mock_switch.assert_called_once_with("develop", force=True)


def test_switch_branch_rolls_back_when_install_fails(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, '[{"doctype": "Role"}]\n')

    with (
        patch.object(App, "head_sha", "abc1234"),
        patch.object(App, "current_branch", "main"),
        patch.object(App, "switch_branch"),
        patch.object(App, "restore_revision") as mock_restore,
        patch.object(App, "record_branch"),
        patch.object(PythonEnvManager, "install_app", side_effect=[CommandError("install failed"), None]),
        patch.object(PythonEnvManager, "build_assets_for_app"),
        pytest.raises(CommandError, match="install failed"),
    ):
        _task(bench).run()

    mock_restore.assert_called_once_with("main", "abc1234", ANY)


def test_switch_branch_rolls_back_when_asset_build_fails(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, '[{"doctype": "Role"}]\n')

    with (
        patch.object(App, "head_sha", "abc1234"),
        patch.object(App, "current_branch", "main"),
        patch.object(App, "switch_branch"),
        patch.object(App, "restore_revision") as mock_restore,
        patch.object(App, "record_branch"),
        patch.object(PythonEnvManager, "install_app"),
        patch.object(
            PythonEnvManager,
            "build_assets_for_app",
            side_effect=[CommandError("build failed"), None],
        ),
        pytest.raises(CommandError, match="build failed"),
    ):
        _task(bench).run()

    mock_restore.assert_called_once_with("main", "abc1234", ANY)


def test_switch_branch_preserves_original_and_rollback_failures(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, '[{"doctype": "Role"}]\n')

    task_error = CommandError("validation exploded")
    rollback_error = CommandError("rollback exploded")

    with (
        patch.object(App, "head_sha", "abc1234"),
        patch.object(App, "current_branch", "main"),
        patch.object(App, "switch_branch"),
        patch.object(App, "validate", side_effect=task_error),
        patch.object(App, "restore_revision", side_effect=rollback_error),
        pytest.raises(ExceptionGroup) as raised,
    ):
        _task(bench).run()

    group = raised.value
    assert "rollback did not complete" in str(group)
    assert group.exceptions == (task_error, rollback_error)


def test_switch_branch_fetch_failure_skips_rollback_reinstall_and_assets(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, '[{"doctype": "Role"}]\n')

    fetch_error = CommandError("fetch failed")

    with (
        patch.object(App, "head_sha", "abc1234"),
        patch.object(App, "current_branch", "main"),
        patch.object(App, "switch_branch", side_effect=fetch_error),
        patch.object(App, "restore_revision") as mock_restore,
        patch.object(PythonEnvManager, "install_app") as mock_install,
        patch.object(PythonEnvManager, "build_assets_for_app") as mock_build,
        pytest.raises(CommandError, match="fetch failed"),
    ):
        _task(bench).run()

    mock_restore.assert_not_called()
    mock_install.assert_not_called()
    mock_build.assert_not_called()


def test_same_commit_failure_still_rolls_back_after_checkout(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, '[{"doctype": "Role"}]\n')

    with (
        patch.object(App, "head_sha", "abc1234"),
        patch.object(App, "current_branch", "main"),
        patch.object(App, "switch_branch"),
        patch.object(App, "restore_revision") as mock_restore,
        patch.object(App, "record_branch"),
        patch.object(PythonEnvManager, "install_app", side_effect=[CommandError("install failed"), None]),
        patch.object(PythonEnvManager, "build_assets_for_app"),
        pytest.raises(CommandError, match="install failed"),
    ):
        _task(bench).run()

    mock_restore.assert_called_once_with("main", "abc1234", ANY)
