"""Tests for PythonAssetBuilder."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.core.app import App
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
    manager.bench.apps_path = app_path.parent
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
        app.has_studio_apps = App.has_studio_apps.fget(app)
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


def test_the_studio_editor_installs_before_frappe_builds_an_app_s_studio_apps(tmp_path: Path) -> None:
    """A prebuilt Frappe has no editor dependencies, and its `after_app_build` hook needs them."""
    app_path = tmp_path / "helpdesk"
    app_path.mkdir()
    editor = tmp_path / "frappe" / "studio" / "frontend"
    write_package(editor)
    (editor / "yarn.lock").write_text("")

    assert ["yarn", "install", "--frozen-lockfile"] not in build_app(app_path)

    (app_path / "studio" / "helpdesk").mkdir(parents=True)
    (app_path / "studio" / "helpdesk" / "helpdesk.json").write_text("{}")
    commands = build_app(app_path)

    assert commands.index(["yarn", "install", "--frozen-lockfile"]) < commands.index(
        ["python", "frappe", "build", "--force", "--app", "helpdesk"]
    )


def test_studio_apps_are_built_with_frappe_s_own_builder(tmp_path: Path) -> None:
    manager = MagicMock()
    manager.bench.apps_path = tmp_path / "apps"
    manager.bench.python = tmp_path / "env" / "bin" / "python"
    builder = PythonAssetBuilder(manager)
    app = make_app(tmp_path / "apps" / "helpdesk", "helpdesk")

    with patch.object(builder, "run_compiler") as compile_studio_apps:
        builder.build_studio_apps(app)
        compile_studio_apps.assert_not_called()  # version-16 has no Studio editor

        write_package(builder.studio_editor.path)
        (builder.studio_editor.path / "node_modules").mkdir()
        (builder.studio_editor.path / "node_modules" / ".yarn-integrity").write_text("")
        builder.build_studio_apps(app)

    assert compile_studio_apps.call_args.args[0] == [
        str(manager.bench.python),
        "-c",
        "from frappe.studio.build import build_standard_apps; build_standard_apps(['helpdesk'])",
    ]


@pytest.mark.parametrize(("archive_has_studio_apps", "builds"), [(False, 1), (True, 0)])
def test_studio_apps_are_built_after_a_download_only_when_the_archive_lacks_them(
    tmp_path: Path, archive_has_studio_apps: bool, builds: int
) -> None:
    builder = make_builder()
    app = make_app(tmp_path / "helpdesk", "helpdesk")
    app.has_page_islands = False
    app.has_studio_apps = True
    prebuilt = MagicMock(has_studio_apps=archive_has_studio_apps, has_page_islands=False)
    prebuilt.install.return_value = True

    with (
        patch("pilot.core.app.prebuilt_assets.PrebuiltAssets", return_value=prebuilt),
        patch.object(builder, "setup_prebuilt_assets"),
        patch.object(builder, "build_studio_apps") as build_studio_apps,
    ):
        assert builder.install_prebuilt_assets(app) is True

    assert build_studio_apps.call_count == builds


def test_a_declared_build_dir_gets_its_dependencies_when_only_the_root_has_them(tmp_path: Path) -> None:
    """Helpdesk installs `desk` from a root postinstall, so an installed root hides a missing `desk`."""
    app_path = tmp_path / "helpdesk"
    write_package(app_path, scripts={"postinstall": "cd desk && yarn install", "build": "cd desk && yarn build"})
    (app_path / "yarn.lock").write_text("")
    (app_path / "node_modules").mkdir()
    (app_path / "node_modules" / ".yarn-integrity").write_text("")
    write_package(app_path / "desk", scripts={"build": "vite build"})
    (app_path / "desk" / "yarn.lock").write_text("")
    (app_path / "pyproject.toml").write_text('[tool.bench.assets]\nbuild_dir = "./desk"\nout_dir = "./helpdesk/public/desk"\n')

    commands = build_app(app_path)

    assert commands == [
        ["yarn", "install", "--frozen-lockfile"],
        ["python", "frappe", "build", "--force", "--app", "helpdesk"],
    ]
