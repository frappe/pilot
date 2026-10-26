"""Tests for App.install's staging: clone, validate, then move into apps/."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config import AppConfig
from pilot.core.app import App
from pilot.core.app.repository import AppRepository
from pilot.exceptions import AppValidationError, BenchError
from pilot.internal.git import GitRepo
from tests.pilot.commands.test_commands import make_bench


def _write_app_tree(path: Path, module: str) -> None:
    (path / module).mkdir(parents=True)
    (path / module / "hooks.py").write_text(f"app_name = '{module}'\n")
    (path / "pyproject.toml").write_text(f'[project]\nname = "{module}"\n')
    (path / ".git").mkdir()


def _make_app(bench, name: str) -> App:
    return App(AppConfig(name=name, repo=f"https://example.com/{name}.git", branch="main"), bench)


def _cloner(module: str, seen: list[Path]):
    """Stand in for the git clone, one level below App.clone so its staging
    logic still runs."""

    def clone(self: AppRepository, on_progress: Callable[[str], None]) -> None:
        seen.append(self.app.path)
        _write_app_tree(self.app.path, module)

    return clone


@pytest.mark.parametrize("phase", ["validation", "environment"])
@pytest.mark.parametrize("existing", [False, True])
def test_install_keeps_original_failure_when_repository_cleanup_fails(tmp_path, phase, existing):
    bench = make_bench(tmp_path)
    bench.create_directories()
    if existing:
        _write_app_tree(bench.apps_path / "myapp", "myapp")
    progress = []
    original = AppValidationError("original install failure")
    move = GitRepo.move

    def move_unless_cleanup(repo, target):
        if target == bench.apps_path / "myapp":
            raise BenchError("cleanup move failed")
        return move(repo, target)

    def remove_unless_cleanup(repo):
        if repo.path.exists():
            raise BenchError("cleanup remove failed")

    with (
        patch.object(AppRepository, "clone", _cloner("myapp", [])),
        patch.object(App, "validate", side_effect=original if phase == "validation" else None),
        patch.object(App, "_install_into_environment", side_effect=original),
        patch.object(App, "_deregister"),
        patch.object(App, "_pip_uninstall"),
        patch.object(App, "promote", return_value=None),
        patch.object(GitRepo, "remove", remove_unless_cleanup),
        patch.object(GitRepo, "move", move_unless_cleanup),
        pytest.raises(AppValidationError, match="original install failure") as caught,
    ):
        _make_app(bench, "myapp").install(on_progress=progress.append)
    assert caught.value is original
    if phase == "validation" or not existing:
        assert any("cleanup" in note for note in original.__notes__)
        assert any("cleanup" in message for message in progress)


def test_install_clones_into_staging_then_moves_into_apps(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    cloned_at: list[Path] = []

    with (
        patch.object(AppRepository, "clone", _cloner("myapp", cloned_at)),
        patch.object(App, "validate"),
        patch.object(App, "_install_into_environment"),
        patch.object(App, "_build_assets_via_env_manager"),
    ):
        result = _make_app(bench, "myapp").install()

    assert cloned_at == [bench.staging_path / "myapp"]
    assert (bench.apps_path / "myapp" / "pyproject.toml").exists()
    assert not (bench.staging_path / "myapp").exists()
    assert result.app.path == bench.apps_path / "myapp"
    assert not result.app.is_staged


def test_install_leaves_apps_empty_when_validation_fails(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()

    with (
        patch.object(AppRepository, "clone", _cloner("myapp", [])),
        patch.object(App, "validate", side_effect=AppValidationError("broken app")),
        pytest.raises(AppValidationError, match="broken app"),
    ):
        _make_app(bench, "myapp").install()

    assert list(bench.apps_path.iterdir()) == []
    assert not (bench.staging_path / "myapp").exists()


def test_install_moves_the_app_under_its_importable_name(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()

    with (
        patch.object(AppRepository, "clone", _cloner("india_compliance", [])),
        patch.object(App, "validate"),
        patch.object(App, "_install_into_environment"),
        patch.object(App, "_build_assets_via_env_manager"),
    ):
        result = _make_app(bench, "india-compliance").install()

    assert (bench.apps_path / "india_compliance" / "india_compliance" / "hooks.py").exists()
    assert not (bench.apps_path / "india-compliance").exists()
    assert result.app.config.name == "india_compliance"


def test_install_stages_an_already_cloned_app_instead_of_re_cloning(tmp_path: Path) -> None:
    """It ends up back in apps/, but is validated outside it - a bench-wide
    operation reads the apps directory, so nothing unvetted may sit there."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app_tree(bench.apps_path / "myapp", "myapp")

    def unexpected_clone(self: AppRepository, on_progress: Callable[[str], None]) -> None:
        raise AssertionError("an app already in apps/ must not be re-cloned")

    validated_at = []
    with (
        patch.object(AppRepository, "clone", unexpected_clone),
        patch.object(App, "validate", lambda self: validated_at.append(self.path)),
        patch.object(App, "_install_into_environment"),
        patch.object(App, "_build_assets_via_env_manager"),
    ):
        result = _make_app(bench, "myapp").install()

    assert validated_at == [bench.staging_path / "myapp"]
    assert result.app.path == bench.apps_path / "myapp"
    assert not (bench.staging_path / "myapp").exists()


def test_install_puts_an_already_cloned_app_back_when_it_fails_validation(tmp_path: Path) -> None:
    """A working tree we did not clone is not ours to delete."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app_tree(bench.apps_path / "myapp", "myapp")
    (bench.apps_path / "myapp" / "local_work.txt").write_text("not ours\n")

    def reject(self: App) -> None:
        raise AppValidationError("nope")

    with (
        patch.object(App, "validate", reject),
        pytest.raises(AppValidationError),
    ):
        _make_app(bench, "myapp").install()

    assert (bench.apps_path / "myapp" / "local_work.txt").read_text() == "not ours\n"
    assert not (bench.staging_path / "myapp").exists()


def test_install_discards_a_staged_clone_left_by_an_interrupted_run(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    stale = bench.staging_path / "myapp"
    _write_app_tree(stale, "myapp")
    (stale / "leftover.txt").write_text("from the run that died\n")

    with (
        patch.object(AppRepository, "clone", _cloner("myapp", [])),
        patch.object(App, "validate"),
        patch.object(App, "_install_into_environment"),
        patch.object(App, "_build_assets_via_env_manager"),
    ):
        _make_app(bench, "myapp").install()

    assert not (bench.apps_path / "myapp" / "leftover.txt").exists()


def test_install_undoes_itself_when_the_asset_build_fails(tmp_path: Path) -> None:
    """A registered app whose assets never built takes the site down with
    '<app> is not installed on site' - so a failure has to leave no trace."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.sites_path / "apps.txt").write_text("frappe\n")

    with (
        patch.object(AppRepository, "clone", _cloner("myapp", [])),
        patch.object(App, "validate"),
        patch.object(App, "_install_into_environment"),
        patch.object(App, "_pip_uninstall") as mock_uninstall,
        patch.object(App, "_build_assets_via_env_manager", side_effect=BenchError("yarn build failed")),
        pytest.raises(BenchError, match="yarn build failed"),
    ):
        _make_app(bench, "myapp").install()

    assert (bench.sites_path / "apps.txt").read_text() == "frappe\n"
    assert not (bench.apps_path / "myapp").exists()
    mock_uninstall.assert_called_once()


def test_install_failure_keeps_a_clone_that_predates_the_run(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app_tree(bench.apps_path / "myapp", "myapp")

    with (
        patch.object(App, "validate"),
        patch.object(App, "_install_into_environment"),
        patch.object(App, "_pip_uninstall"),
        patch.object(App, "_build_assets_via_env_manager", side_effect=BenchError("yarn build failed")),
        pytest.raises(BenchError),
    ):
        _make_app(bench, "myapp").install()

    assert (bench.apps_path / "myapp" / "pyproject.toml").exists()
    assert "myapp" not in (bench.sites_path / "apps.txt").read_text()


def test_install_records_the_branch_only_once_it_succeeds(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    bench.config.write(bench.path)

    with (
        patch.object(AppRepository, "clone", _cloner("myapp", [])),
        patch.object(App, "validate"),
        patch.object(App, "_install_into_environment"),
        patch.object(App, "_pip_uninstall"),
        patch.object(App, "_build_assets_via_env_manager", side_effect=BenchError("yarn build failed")),
        pytest.raises(BenchError),
    ):
        _make_app(bench, "myapp").install()

    assert "myapp" not in (bench.path / "bench.toml").read_text()


def test_promote_refuses_to_overwrite_an_existing_app(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.apps_path / "myapp").mkdir()
    staged = App(AppConfig(name="myapp", repo="", branch="main"), bench, staged=True)
    _write_app_tree(staged.path, "myapp")

    with pytest.raises(BenchError, match="already exists"):
        staged.promote()
