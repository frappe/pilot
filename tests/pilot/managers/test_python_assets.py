"""Tests for PythonAssetBuilder."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.config.build import BuildConfig
from pilot.core.app import App
from pilot.exceptions import BenchError, CommandError
from pilot.managers import frappe_asset_command
from pilot.managers.python_assets import PythonAssetBuilder


def make_app(app_path: Path, name: str = "gameplan") -> MagicMock:
    app = MagicMock()
    app.path = app_path
    app.config.name = name
    return app


def make_builder(tmp_path: Path) -> PythonAssetBuilder:
    manager = MagicMock()
    manager.bench.config.build = BuildConfig()
    manager.bench.path = tmp_path
    manager.bench.apps_path = tmp_path / "apps"
    return PythonAssetBuilder(manager)


def build_app(app_path: Path, on_install: Callable[[], None] = lambda: None) -> list[list[str]]:
    """Build `app_path` from source; return every command it ran, installs included."""
    manager = MagicMock()
    manager.bench.config.build = BuildConfig()
    manager.bench.frappe_call = ["python"]
    manager.bench.sites_path = app_path.parent / "sites"
    manager.bench.apps_path = app_path.parent
    manager.bench.path = app_path.parent
    builder = PythonAssetBuilder(manager)
    commands: list[list[str]] = []

    def record(argv: list[str], **kwargs: object) -> None:
        commands.append(argv)

    def install(argv: list[str], **kwargs: object) -> None:
        record(argv)
        on_install()

    with (
        patch("pilot.core.app.node_packages.shutil.which", side_effect=lambda name: name),
        patch("pilot.managers.node_dependencies.NodeDependencies.get_key", return_value="installed"),
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


def test_full_build_installs_nested_dependencies_before_compiling(tmp_path: Path) -> None:
    app_path = tmp_path / "app"
    directories = [app_path, app_path / "frontend", app_path / "roster"]
    for directory in directories:
        write_package(directory)
    builder = make_builder(tmp_path)
    builder.bench.apps.return_value = [make_app(app_path)]
    installed = []

    def install(argv, **kwargs):
        installed.append(kwargs["cwd"])

    def compile_assets(*args, **kwargs):
        assert installed == directories

    with (
        patch("pilot.core.app.node_packages.shutil.which", side_effect=lambda name: name),
        patch("pilot.core.app.node_packages.run_command", side_effect=install),
        patch.object(builder, "run_compiler", side_effect=compile_assets) as compiler,
    ):
        builder.build_assets()
    compiler.assert_called_once()


def test_full_build_does_not_fingerprint_or_cache_assets(tmp_path):
    builder = make_builder(tmp_path)
    builder.bench.apps.return_value = []
    with (
        patch(
            "pilot.core.bench.build_artifacts.BuildArtifacts.get_key",
            side_effect=AssertionError("a normal build must not fingerprint the bench"),
        ),
        patch.object(builder, "run_compiler") as compiler,
    ):
        builder.build_assets()
    compiler.assert_called_once()


def test_frappe_adapter_preserves_the_bench_import_path(tmp_path: Path) -> None:
    frappe = tmp_path / "frappe"
    (frappe / "utils").mkdir(parents=True)
    (frappe / "__init__.py").touch()
    (frappe / "bundler.py").write_text("from redis import expected_client\nlink_assets_dir = None\n")
    (frappe / "utils" / "bench_helper.py").write_text(
        "from frappe import bundler\ndef main():\n    assert callable(bundler.link_assets_dir)\n"
    )
    (tmp_path / "redis.py").write_text("expected_client = True\n")

    subprocess.run(
        [sys.executable, frappe_asset_command.__file__, "frappe", "build", "--force"],
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
        check=True,
    )


def test_page_island_resolver_matches_importers_through_the_staged_apps_link(tmp_path: Path) -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable.")
    hooks_type = subprocess.check_output([node, "-p", "typeof require('node:module').registerHooks"], text=True)
    if hooks_type.strip() != "function":
        pytest.skip("Node module resolution hooks are unavailable.")

    apps = tmp_path / "apps"
    islands = apps / "frappe" / "ui" / "vite" / "island"
    islands.mkdir(parents=True)
    (islands / "package.json").write_text('{"type":"module"}')
    (islands / "pages.js").write_text(
        "export function discoverPageIslands(root) { return {dirs: [root + '/apps/frappe']}; }"
    )
    script = islands / "build-pages.js"
    script.write_text(
        "import { discoverPageIslands } from './pages.js';\n"
        "console.log(discoverPageIslands(process.env.FRAPPE_BENCH_ROOT).dirs[0]);\n"
    )
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "apps").symlink_to(apps)
    preload = Path(frappe_asset_command.__file__).with_name("frappe_page_islands.mjs").as_uri()

    result = subprocess.check_output(
        [node, "--import", preload, str(script)], env={**os.environ, "FRAPPE_BENCH_ROOT": str(stage)}, text=True
    )

    assert result.strip() == str(apps / "frappe")


@pytest.mark.parametrize("compiler_fails", [False, True])
def test_source_build_keeps_live_assets_until_publication(tmp_path: Path, compiler_fails: bool) -> None:
    builder = make_builder(tmp_path)
    bench = builder.bench
    bench.path = tmp_path
    bench.apps_path = tmp_path / "apps"
    bench.sites_path = tmp_path / "sites"
    builder.manager._build_env.return_value = {}
    app = make_app(bench.apps_path / "gameplan")
    app.has_studio_apps = False
    bench.apps.return_value = [app]
    esbuild = bench.apps_path / "frappe" / "esbuild"
    esbuild.mkdir(parents=True)
    (esbuild / "utils.js").write_text("process.env.FRAPPE_BENCH_ROOT")
    dist = app.path / "gameplan" / "public" / "dist" / "js"
    dist.mkdir(parents=True)
    old_bundle = "gameplan.bundle.AAAAAAAA.js"
    new_bundle = "gameplan.bundle.BBBBBBBB.js"
    (dist / old_bundle).write_text("old")
    assets = bench.sites_path / "assets"
    assets.mkdir(parents=True)
    manifest = assets / "assets.json"
    old_map = {"gameplan.bundle.js": f"/assets/gameplan/dist/js/{old_bundle}"}
    new_map = {"gameplan.bundle.js": f"/assets/gameplan/dist/js/{new_bundle}"}
    manifest.write_text(json.dumps(old_map))

    def compile_assets(argv, *, env, **kwargs):
        staged_assets = Path(env["FRAPPE_BENCH_ROOT"]) / "sites" / "assets"
        output = staged_assets / "gameplan" / "dist" / "js"
        output.mkdir(parents=True, exist_ok=True)
        (output / new_bundle).write_text("new")
        (staged_assets / "assets.json").write_text(json.dumps(new_map))
        assert (dist / old_bundle).read_text() == "old"
        assert not (dist / new_bundle).exists()
        assert json.loads(manifest.read_text()) == old_map
        if compiler_fails:
            raise CommandError("build failed")

    with (
        patch.object(builder, "run_compiler", side_effect=compile_assets),
        patch.object(builder, "invalidate_asset_manifest") as invalidate,
    ):
        if compiler_fails:
            with pytest.raises(CommandError, match="build failed"):
                builder.build_assets_for_app(app, force=True)
            invalidate.assert_not_called()
        else:
            builder.build_assets_for_app(app, force=True)
            invalidate.assert_called_once()

    assert (dist / old_bundle).read_text() == "old"
    assert (dist / new_bundle).exists() != compiler_fails
    assert json.loads(manifest.read_text()) == (old_map if compiler_fails else new_map)


def test_page_islands_are_built_with_frappe_s_own_script(tmp_path: Path) -> None:
    manager = MagicMock()
    manager.bench.config.build = BuildConfig()
    manager.bench.apps_path = tmp_path / "apps"
    builder = PythonAssetBuilder(manager)
    script = tmp_path / "apps" / "frappe" / "ui" / "vite" / "island" / "build-pages.js"

    with (
        patch.object(builder, "run_staged_assets") as compile_islands,
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
    builder = make_builder(tmp_path)
    app = make_app(tmp_path / "helpdesk", "helpdesk")
    app.path.mkdir()
    app.has_page_islands = False
    app.has_studio_apps = True
    prebuilt = MagicMock(has_studio_apps=archive_has_studio_apps, has_page_islands=False)
    prebuilt.install.return_value = True

    with (
        patch("pilot.core.app.prebuilt_assets.PrebuiltAssets", return_value=prebuilt),
        patch.object(builder, "setup_prebuilt_assets"),
        patch.object(builder, "invalidate_asset_manifest"),
        patch.object(builder, "build_studio_apps") as build_studio_apps,
    ):
        assert builder.install_prebuilt_assets(app) is True

    assert build_studio_apps.call_count == builds


def test_skip_frontend_downloads_prebuilt_assets_without_installing_or_building(tmp_path: Path) -> None:
    """A frontend-skipping run with a published archive touches neither JS installs nor compilers."""
    builder = make_builder(tmp_path)
    app = make_app(tmp_path / "frappe", "frappe")
    app.is_linked = False
    app.has_source_changes = False
    app.has_page_islands = True
    app.has_studio_apps = True
    write_package(tmp_path / "frappe")
    prebuilt = MagicMock(has_studio_apps=False, has_page_islands=False)
    prebuilt.install.return_value = True

    with (
        patch("pilot.core.app.prebuilt_assets.PrebuiltAssets", return_value=prebuilt),
        patch.object(builder, "setup_prebuilt_assets"),
        patch.object(builder, "invalidate_asset_manifest"),
        patch.object(builder, "build_page_islands") as build_page_islands,
        patch.object(builder, "build_studio_apps") as build_studio_apps,
        patch.object(builder, "run_compiler") as run_compiler,
        patch("pilot.core.app.node_packages.run_command") as run_command,
    ):
        builder.build_assets_for_app(app, skip_frontend=True)

    prebuilt.install.assert_called_once()
    run_command.assert_not_called()
    run_compiler.assert_not_called()
    build_page_islands.assert_not_called()
    build_studio_apps.assert_not_called()


def test_skip_frontend_falls_back_to_a_full_build_without_an_archive(tmp_path: Path) -> None:
    """A frontend-skipping run without a published archive installs and builds like normal."""
    app_path = tmp_path / "frappe"
    write_package(app_path)
    (app_path / "yarn.lock").write_text("")
    manager = MagicMock()
    manager.bench.config.build = BuildConfig()
    manager.bench.frappe_call = ["python"]
    manager.bench.sites_path = app_path.parent / "sites"
    manager.bench.apps_path = app_path.parent
    manager.bench.path = app_path.parent
    builder = PythonAssetBuilder(manager)
    commands: list[list[str]] = []

    def install(argv: list[str], **kwargs: object) -> None:
        commands.append(argv)
        if argv[:2] == ["yarn", "install"]:
            (app_path / "node_modules").mkdir(exist_ok=True)
            (app_path / "node_modules" / ".yarn-integrity").write_text("")

    app = make_app(app_path, "frappe")
    app.is_linked = False
    app.has_source_changes = False
    prebuilt = MagicMock(has_studio_apps=False, has_page_islands=False)
    prebuilt.install.return_value = False

    with (
        patch("pilot.core.app.node_packages.shutil.which", side_effect=lambda name: name),
        patch("pilot.core.app.node_packages.run_command", side_effect=install),
        patch("pilot.core.app.prebuilt_assets.PrebuiltAssets", return_value=prebuilt),
        patch.object(builder, "run_compiler", side_effect=lambda argv, **kwargs: commands.append(argv)),
    ):
        builder.build_assets_for_app(app, skip_frontend=True)

    assert ["yarn", "install", "--frozen-lockfile"] in commands
    assert ["python", "frappe", "build", "--force", "--app", "frappe"] in commands


def test_a_declared_build_dir_gets_its_dependencies_when_only_the_root_has_them(tmp_path: Path) -> None:
    """Helpdesk installs `desk` from a root postinstall, so an installed root hides a missing `desk`."""
    app_path = tmp_path / "helpdesk"
    write_package(app_path, scripts={"postinstall": "cd desk && yarn install", "build": "cd desk && yarn build"})
    (app_path / "yarn.lock").write_text("")
    (app_path / "node_modules").mkdir()
    (app_path / "node_modules" / ".yarn-integrity").write_text("{}")
    (app_path / "node_modules" / ".pilot-install-key").write_text("installed")
    write_package(app_path / "desk", scripts={"build": "vite build"})
    (app_path / "desk" / "yarn.lock").write_text("")
    (app_path / "pyproject.toml").write_text('[tool.bench.assets]\nbuild_dir = "./desk"\nout_dir = "./helpdesk/public/desk"\n')

    commands = build_app(app_path)

    assert commands == [
        ["yarn", "install", "--frozen-lockfile"],
        ["python", "frappe", "build", "--force", "--app", "helpdesk"],
    ]
