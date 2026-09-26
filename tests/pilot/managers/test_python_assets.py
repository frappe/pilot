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


def test_build_assets_passes_node_heap_env_to_frappe_build(tmp_path: Path) -> None:
    manager = MagicMock()
    manager.bench.apps.return_value = []
    manager.bench.frappe_call = ["python"]
    manager.bench.sites_path = tmp_path / "sites"
    manager._build_env.return_value = {"PATH": "/usr/bin"}
    builder = PythonAssetBuilder(manager)

    with (
        patch.object(builder, "auto_node_heap_mb", return_value=4096),
        patch("pilot.managers.python_assets.run_command") as run_command,
    ):
        builder.build_assets()

    run_command.assert_called_once()
    assert run_command.call_args.kwargs["env"]["NODE_OPTIONS"] == "--max-old-space-size=4096"
    assert run_command.call_args.kwargs["env"]["PATH"] == "/usr/bin"


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


def test_build_assets_for_app_passes_node_heap_env_to_all_node_builds(tmp_path: Path) -> None:
    app_path = tmp_path / "crm"
    frontend_dir = app_path / "frontend"
    frontend_dir.mkdir(parents=True)
    (frontend_dir / "package.json").write_text("{}")

    manager = MagicMock()
    manager.bench.frappe_call = ["python"]
    manager.bench.sites_path = tmp_path / "sites"
    manager._build_env.return_value = {"PATH": "/usr/bin"}
    builder = PythonAssetBuilder(manager)

    with (
        patch("pilot.managers.python_assets.git_has_local_changes", return_value=True),
        patch.object(builder, "ensure_yarn_install"),
        patch.object(builder, "auto_node_heap_mb", return_value=4096),
        patch("pilot.managers.python_assets.get_yarn_bin", return_value="yarn"),
        patch("pilot.managers.python_assets.run_command") as run_command,
    ):
        builder.build_assets_for_app(make_app(app_path, "crm"))

    frappe_call = next(
        call for call in run_command.call_args_list if "frappe" in call.args[0]
    )
    frontend_call = next(
        call for call in run_command.call_args_list if call.args[0] == ["yarn", "build"]
    )

    for build_call in [frappe_call, frontend_call]:
        assert build_call.kwargs["env"]["NODE_OPTIONS"] == "--max-old-space-size=4096"
        assert build_call.kwargs["env"]["PATH"] == "/usr/bin"


def test_auto_node_heap_uses_sixty_percent_of_available_memory() -> None:
    builder = make_builder()
    with patch.object(builder, "available_memory_mb", return_value=8192):
        assert builder.auto_node_heap_mb() == 4915


def test_auto_node_heap_enforces_minimum() -> None:
    builder = make_builder()
    with patch.object(builder, "available_memory_mb", return_value=1024):
        assert builder.auto_node_heap_mb() == 2048


def test_auto_node_heap_enforces_maximum() -> None:
    builder = make_builder()
    with patch.object(builder, "available_memory_mb", return_value=32768):
        assert builder.auto_node_heap_mb() == 6144


def test_node_build_env_uses_admin_override() -> None:
    manager = MagicMock()
    manager._build_env.return_value = {
        "PATH": "/usr/bin",
        "PILOT_NODE_MAX_OLD_SPACE_SIZE": "4096",
    }
    builder = PythonAssetBuilder(manager)

    with patch.object(builder, "auto_node_heap_mb") as auto_heap:
        env = builder.node_build_env()

    auto_heap.assert_not_called()
    assert env["NODE_OPTIONS"] == "--max-old-space-size=4096"


def test_node_build_env_preserves_existing_node_options() -> None:
    manager = MagicMock()
    manager._build_env.return_value = {"NODE_OPTIONS": "--trace-warnings"}
    builder = PythonAssetBuilder(manager)

    with patch.object(builder, "auto_node_heap_mb", return_value=3072):
        env = builder.node_build_env()

    assert env["NODE_OPTIONS"] == "--trace-warnings --max-old-space-size=3072"


def test_node_build_env_invalid_override_falls_back_to_auto() -> None:
    manager = MagicMock()
    manager._build_env.return_value = {"PILOT_NODE_MAX_OLD_SPACE_SIZE": "invalid"}
    builder = PythonAssetBuilder(manager)

    with patch.object(builder, "auto_node_heap_mb", return_value=3584) as auto_heap:
        env = builder.node_build_env()

    auto_heap.assert_called_once_with()
    assert env["NODE_OPTIONS"] == "--max-old-space-size=3584"


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


def test_ensure_yarn_install_skips_when_integrity_is_current(tmp_path: Path) -> None:
    """Do not reinstall when node_modules integrity is newer than yarn.lock."""
    lock = tmp_path / "yarn.lock"
    integrity = tmp_path / "node_modules" / ".yarn-integrity"
    integrity.parent.mkdir()
    lock.write_text("lockfile")
    integrity.write_text("integrity")
    os.utime(lock, (1, 1))
    os.utime(integrity, (2, 2))

    with patch("pilot.managers.python_assets.run_command") as run_command:
        make_builder().ensure_yarn_install(tmp_path)

    run_command.assert_not_called()
