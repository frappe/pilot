"""Tests for PythonAssetBuilder."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from pilot.managers.python_assets import PythonAssetBuilder


def make_app(app_path: Path, name: str = "gameplan") -> MagicMock:
    app = MagicMock()
    app.path = app_path
    app.config.name = name
    return app


def make_builder() -> PythonAssetBuilder:
    manager = MagicMock()
    return PythonAssetBuilder(manager)


def test_build_assets_for_app_installs_js_deps_before_frappe_build_runs(tmp_path: Path) -> None:
    """frappe's own `bench build` step shells into `frontend` and runs `yarn build` there,
    so node_modules must be synced before that step, not only in the standalone loop after it."""
    app_path = tmp_path / "gameplan"
    frontend_dir = app_path / "frontend"
    frontend_dir.mkdir(parents=True)
    (frontend_dir / "package.json").write_text("{}")

    manager = MagicMock()
    manager.bench.frappe_call = ["python"]
    manager.bench.sites_path = tmp_path / "sites"
    builder = PythonAssetBuilder(manager)

    events: list[str] = []

    with (
        patch("pilot.managers.python_assets.git_has_local_changes", return_value=True),
        patch(
            "pilot.managers.python_assets.run_command",
            side_effect=lambda *a, **k: events.append("run_command"),
        ),
        patch.object(
            builder,
            "ensure_yarn_install",
            side_effect=lambda path: events.append(f"ensure_yarn_install:{path.name}"),
        ),
    ):
        builder.build_assets_for_app(make_app(app_path))

    assert events.index("ensure_yarn_install:frontend") < events.index("run_command")


def test_ensure_yarn_install_uses_frozen_lockfile_first(tmp_path: Path) -> None:
    """A healthy lockfile should keep the reproducible frozen install path."""
    (tmp_path / "yarn.lock").write_text("lockfile")

    with (
        patch("pilot.managers.python_assets.get_yarn_bin", return_value="yarn"),
        patch("pilot.managers.python_assets.run_command") as run_command,
    ):
        make_builder().ensure_yarn_install(tmp_path)

    run_command.assert_called_once_with(
        ["yarn", "install", "--frozen-lockfile"],
        cwd=tmp_path,
        stream_output=True,
    )


def test_ensure_yarn_install_falls_back_to_pure_lockfile(tmp_path: Path) -> None:
    """An out-of-sync lockfile should retry without modifying yarn.lock."""
    (tmp_path / "yarn.lock").write_text("lockfile")

    with (
        patch("pilot.managers.python_assets.get_yarn_bin", return_value="yarn"),
        patch(
            "pilot.managers.python_assets.run_command",
            side_effect=[RuntimeError("lockfile needs update"), None],
        ) as run_command,
    ):
        make_builder().ensure_yarn_install(tmp_path)

    assert run_command.call_count == 2
    assert run_command.call_args_list[0].args[0] == ["yarn", "install", "--frozen-lockfile"]
    assert run_command.call_args_list[1].args[0] == ["yarn", "install", "--pure-lockfile"]
    assert run_command.call_args_list[1].kwargs == {"cwd": tmp_path, "stream_output": True}


def test_ensure_yarn_install_checks_contents_even_when_timestamps_match(tmp_path: Path) -> None:
    from pilot.managers.node_dependencies import NodeDependencies

    lock = tmp_path / "yarn.lock"
    integrity = tmp_path / "node_modules" / ".yarn-integrity"
    integrity.parent.mkdir()
    lock.write_text("lockfile")
    integrity.write_text("integrity")
    os.utime(lock, (1, 1))
    os.utime(integrity, (2, 2))
    (integrity.parent / ".pilot-install-key").write_text(NodeDependencies.get_key(tmp_path))

    with patch("pilot.managers.python_assets.run_command") as run_command:
        make_builder().ensure_yarn_install(tmp_path)

    run_command.assert_not_called()
    lock.write_text("changed lockfile")
    os.utime(lock, (1, 1))
    with patch("pilot.managers.python_assets.run_command") as run_command:
        make_builder().ensure_yarn_install(tmp_path)
    run_command.assert_called_once()


def test_full_build_installs_nested_dependencies_before_compiling(tmp_path: Path) -> None:
    app_path = tmp_path / "app"
    for relative in (".", "frontend", "roster"):
        directory = app_path / relative
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "package.json").write_text("{}")
    builder = make_builder()
    builder.bench.apps.return_value = [make_app(app_path)]
    installed = []

    def compile_assets(*args, **kwargs):
        assert installed == [app_path, app_path / "frontend", app_path / "roster"]

    with (
        patch("pilot.core.bench.build_artifacts.BuildArtifacts.get_key", return_value="key"),
        patch("pilot.core.bench.build_artifacts.BuildArtifacts.capture"),
        patch.object(builder, "ensure_yarn_install", side_effect=installed.append),
        patch.object(builder, "run_compiler", side_effect=compile_assets) as compiler,
    ):
        builder.build_assets()
    compiler.assert_called_once()
