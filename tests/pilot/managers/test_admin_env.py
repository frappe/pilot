"""Tests for admin venv dependency reconciliation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.managers.environment import AdminEnvManager


def _make_manager(root: Path, deps: str = '["cowsay"]') -> AdminEnvManager:
    (root / "pyproject.toml").write_text(
        f"[project.optional-dependencies]\nadmin = {deps}\n"
    )
    return AdminEnvManager(root)


def _existing_venv(root: Path) -> None:
    bin_dir = root / ".admin-venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").touch()


@pytest.fixture
def uv_runs(tmp_path: Path):
    def fake_run(command, **kwargs):
        if "venv" in command:
            _existing_venv(tmp_path)

    with (
        patch("pilot.managers.environment.shutil.which", return_value="uv"),
        patch("pilot.managers.environment.subprocess.run", side_effect=fake_run) as run,
    ):
        yield run


def test_ensure_installs_deps_added_after_venv_was_created(tmp_path: Path, uv_runs) -> None:
    _existing_venv(tmp_path)
    _make_manager(tmp_path).ensure()

    installed = [call.args[0] for call in uv_runs.call_args_list if "pip" in call.args[0]]
    assert installed, "existing venv should still reconcile admin dependencies"
    assert "cowsay" in installed[0]


def test_ensure_creates_venv_and_installs_deps(tmp_path: Path, uv_runs) -> None:
    _make_manager(tmp_path).ensure()

    commands = [call.args[0] for call in uv_runs.call_args_list]
    assert any("venv" in command for command in commands)
    assert any("cowsay" in command for command in commands)


def test_ensure_skips_install_when_deps_unchanged(
    tmp_path: Path, uv_runs, capsys: pytest.CaptureFixture[str]
) -> None:
    _existing_venv(tmp_path)
    manager = _make_manager(tmp_path)
    manager.ensure()
    uv_runs.reset_mock()
    capsys.readouterr()

    manager.ensure()

    assert not [call for call in uv_runs.call_args_list if "pip" in call.args[0]]
    assert capsys.readouterr().out == ""


def test_ensure_reinstalls_when_deps_change(tmp_path: Path, uv_runs) -> None:
    _existing_venv(tmp_path)
    _make_manager(tmp_path).ensure()
    uv_runs.reset_mock()

    _make_manager(tmp_path, deps='["cowsay", "flask"]').ensure()

    installed = [call.args[0] for call in uv_runs.call_args_list if "pip" in call.args[0]]
    assert installed and "flask" in installed[0]
