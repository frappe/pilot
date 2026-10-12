"""Tests for PythonAssetBuilder."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.exceptions import BenchError
from pilot.managers.python_assets import PythonAssetBuilder


def make_app(app_path: Path, name: str = "gameplan") -> MagicMock:
    app = MagicMock()
    app.path = app_path
    app.config.name = name
    return app


def make_builder() -> PythonAssetBuilder:
    manager = MagicMock()
    return PythonAssetBuilder(manager)


def build_app(app_path: Path, on_install: Callable[[], None] = lambda: None) -> list[list[str]]:
    """Build `app_path` from source; return every command it ran, installs included."""
    manager = MagicMock()
    manager.bench.frappe_call = ["python"]
    manager.bench.sites_path = app_path.parent / "sites"
    builder = PythonAssetBuilder(manager)
    commands: list[list[str]] = []

    def record(argv: list[str], **kwargs: object) -> None:
        commands.append(argv)

    def install(argv: list[str], **kwargs: object) -> None:
        record(argv)
        on_install()

    with (
        patch("pilot.core.app.node_packages.shutil.which", side_effect=lambda name: name),
        patch("pilot.core.app.node_packages.run_command", side_effect=install),
        patch.object(builder, "run_compiler", side_effect=record),
    ):
        app = make_app(app_path, app_path.name)
        app.has_source_changes = True
        builder.build_assets_for_app(app)
    return commands


def write_package(path: Path, **package: object) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "package.json").write_text(json.dumps(package))


def test_frontend_dependencies_install_before_frappe_build_runs(tmp_path: Path) -> None:
    """frappe's own `bench build` step runs the app's build script, which builds `frontend`,
    so node_modules there must be synced before that step."""
    app_path = tmp_path / "gameplan"
    write_package(app_path / "frontend")
    (app_path / "frontend" / "yarn.lock").write_text("")

    commands = build_app(app_path)

    assert commands.index(["yarn", "install", "--frozen-lockfile"]) < commands.index(
        ["python", "frappe", "build", "--force", "--app", "gameplan"]
    )


def test_pnpm_workspace_installs_once_and_builds_once(tmp_path: Path) -> None:
    app_path = tmp_path / "suite"
    write_package(app_path, scripts={"build": "pnpm --filter frontend build"})
    (app_path / "pnpm-lock.yaml").write_text("importers:\n\n  .: {}\n\n  frontend: {}\n")
    write_package(app_path / "frontend", scripts={"build": "vite build"})

    def write_install_marker() -> None:
        (app_path / "node_modules").mkdir(exist_ok=True)
        (app_path / "node_modules" / ".modules.yaml").write_text("")

    commands = build_app(app_path, on_install=write_install_marker)

    assert commands == [
        ["pnpm", "install", "--frozen-lockfile", "--config.confirmModulesPurge=false"],
        ["python", "frappe", "build", "--force", "--app", "suite"],
    ]


def test_frontend_builds_after_frappe_build_when_the_app_has_no_build_script(tmp_path: Path) -> None:
    app_path = tmp_path / "helpdesk"
    write_package(app_path)
    write_package(app_path / "frontend", scripts={"build": "vite build"})
    (app_path / "frontend" / "pnpm-lock.yaml").write_text("")

    commands = build_app(app_path)

    assert commands[-1] == ["pnpm", "run", "build"]


def test_frontend_with_two_lockfiles_fails_before_any_install(tmp_path: Path) -> None:
    app_path = tmp_path / "helpdesk"
    write_package(app_path)
    (app_path / "yarn.lock").write_text("")
    write_package(app_path / "frontend")
    (app_path / "frontend" / "yarn.lock").write_text("")
    (app_path / "frontend" / "pnpm-lock.yaml").write_text("")

    with pytest.raises(BenchError, match=r"yarn\.lock and pnpm-lock\.yaml"):
        build_app(app_path, on_install=lambda: pytest.fail("installed before the lockfile check"))


def test_page_islands_are_built_with_frappe_s_own_script(tmp_path: Path) -> None:
    manager = MagicMock()
    manager.bench.apps_path = tmp_path / "apps"
    builder = PythonAssetBuilder(manager)
    script = tmp_path / "apps" / "frappe" / "ui" / "vite" / "island" / "build-pages.js"

    with (
        patch.object(builder, "run_compiler") as compile_islands,
        patch("pilot.managers.python_assets.NodePackages"),
    ):
        builder.build_page_islands()
        compile_islands.assert_not_called()  # version-16 has no page islands

        script.parent.mkdir(parents=True)
        script.write_text("")
        builder.build_page_islands()

    assert compile_islands.call_args.args[0] == ["node", str(script), "--production"]
