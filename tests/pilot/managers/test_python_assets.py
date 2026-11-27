"""Tests for PythonAssetBuilder."""

from __future__ import annotations

import json
import shutil
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
    app.module_name = name.replace("-", "_")
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
    builder = make_builder()
    builder.bench.sites_path = tmp_path / "sites"
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


def test_full_build_does_not_fingerprint_or_cache_assets(tmp_path: Path):
    builder = make_builder()
    builder.bench.sites_path = tmp_path / "sites"
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


def test_manifest_entries_whose_files_are_gone_name_their_apps(tmp_path: Path) -> None:
    """A rebuild that died before writing the manifest leaves old hashes that 404."""
    manager = MagicMock()
    manager.bench.sites_path = tmp_path / "sites"
    assets = manager.bench.sites_path / "assets"
    (assets / "frappe" / "dist" / "css").mkdir(parents=True)
    (assets / "erpnext" / "dist" / "css").mkdir(parents=True)
    (assets / "erpnext" / "dist" / "css" / "web.bundle.ABCDEFGH.css").write_text("")
    manager.bench.apps.return_value = [make_app(tmp_path, name) for name in ("frappe", "erpnext", "hrms")]
    (assets / "assets.json").write_text(
        json.dumps(
            {
                "website.bundle.css": "/assets/frappe/dist/css/website.bundle.OLDHASH1.css",
                "web.bundle.css": "/assets/erpnext/dist/css/web.bundle.ABCDEFGH.css",
                "hrms.bundle.js": "/assets/hrms/dist/js/hrms.bundle.GONEJS01.js",
                "removed.bundle.js": "/assets/removed/dist/js/removed.bundle.GONEJS01.js",
            }
        )
    )
    (assets / "assets-rtl.json").write_text(
        json.dumps({"rtl_website.bundle.css": "/assets/frappe/dist/css-rtl/website.bundle.OLDHASH2.css"})
    )

    assert PythonAssetBuilder(manager).apps_with_removed_assets() == ["frappe", "hrms"]


def test_unreadable_manifests_do_not_block_a_build(tmp_path: Path) -> None:
    manager = MagicMock()
    manager.bench.sites_path = tmp_path / "sites"
    (manager.bench.sites_path / "assets").mkdir(parents=True)
    (manager.bench.sites_path / "assets" / "assets.json").write_text("{not json")
    manager.bench.apps.return_value = []

    assert PythonAssetBuilder(manager).apps_with_removed_assets() == []


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
    # Frappe's script runs yarn, so it needs the build PATH that finds yarn.
    assert compile_islands.call_args.kwargs["env"] is manager._build_env.return_value


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
    manager.bench.python_argv = [str(manager.bench.python), "-P"]
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
        "-P",
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


def test_skip_frontend_downloads_prebuilt_assets_without_installing_or_building(tmp_path: Path) -> None:
    """A frontend-skipping run with a published archive touches neither JS installs nor compilers."""
    builder = make_builder()
    builder.bench.sites_path = tmp_path / "sites"
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
    manager.bench.frappe_call = ["python"]
    manager.bench.sites_path = app_path.parent / "sites"
    manager.bench.apps_path = app_path.parent
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


def test_rebuild_preserves_open_tabs_and_retires_older_bundles(tmp_path: Path) -> None:
    builder = make_builder()
    builder.bench.sites_path = tmp_path / "sites"
    builder.bench.config.production.enabled = True
    app = make_app(tmp_path / "apps/gameplan")
    app.has_source_changes = True
    dist = app.path / "gameplan/public/dist/js"
    dist.mkdir(parents=True)
    old, unchanged, retired = ("old.bundle.OLD00000.js", "libs.bundle.SAME0000.js", "retired.js")
    for name in (old, unchanged, retired):
        (dist / name).write_text("original")
    assets = builder.bench.sites_path / "assets"
    assets.mkdir(parents=True)
    (assets / "assets.json").write_text(
        json.dumps({name: f"/assets/gameplan/dist/js/{name}" for name in (old, unchanged)})
    )

    def compile_assets(app):
        shutil.rmtree(dist)
        dist.mkdir(parents=True)
        (dist / unchanged).write_bytes(b"")
        priority = tmp_path / ".previous-bundles/building/assets/gameplan/dist/js"
        assert (priority / old).read_text() == "original"
        assert (priority / unchanged).read_text() == "original"
        (dist / unchanged).write_text("rebuilt")

    with (
        patch.object(builder, "prepare_asset_fallback"),
        patch.object(builder, "build_app_from_source", side_effect=compile_assets),
    ):
        builder.build_assets_for_app(app)

    assert (tmp_path / ".previous-bundles/assets/gameplan/dist/js" / old).read_text() == "original"
    assert (dist / unchanged).read_text() == "rebuilt"
    assert not (dist / retired).exists()
    assert not (tmp_path / ".previous-bundles/building/assets/gameplan").exists()


@pytest.mark.parametrize("route", ["source", "prebuilt", "whole-bench"])
def test_island_builds_keep_the_previous_entry_chunks_and_media(tmp_path: Path, route: str) -> None:
    builder = make_builder()
    builder.bench.sites_path = tmp_path / "sites"
    builder.bench.config.production.enabled = True
    builder.bench.apps_path = tmp_path / "apps"
    app = make_app(builder.bench.apps_path / "gameplan")
    frappe = make_app(builder.bench.apps_path / "frappe", "frappe")
    builder.bench.apps.return_value = [app, frappe]
    builder.page_island_script.parent.mkdir(parents=True)
    builder.page_island_script.touch()
    relative = Path("dist/page-island")
    public = frappe.path / "frappe" / "public"
    islands = public / relative
    fallback = tmp_path / ".previous-bundles" / "assets" / "frappe" / relative
    manifest = builder.bench.sites_path / "assets" / "assets.json"
    manifest.parent.mkdir(parents=True)
    unrelated = fallback.parent / "js" / "desk.old.js"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_text("old desk")

    def names(generation: str) -> set[str]:
        return {f"board.{generation}.js", f"chunks/shared.{generation}.js", f"media/icon.{generation}.svg"}

    def write_generation(generation: str) -> None:
        for filename in names(generation):
            path = islands / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(generation)
        manifest.write_text(
            json.dumps({"gameplan.board": f"/assets/frappe/{relative}/board.{generation}.js"})
        )

    generation = "one"
    write_generation(generation)

    def compile_islands(*args, **kwargs):
        shutil.rmtree(islands)
        for filename in names(generation):
            assert (fallback / filename).read_text() == generation
        write_generation({"one": "two", "two": "three"}[generation])

    with (
        patch.object(builder, "prepare_asset_fallback"),
        patch.object(builder, "run_compiler", side_effect=compile_islands),
        patch.object(builder, "build_app_from_source", side_effect=compile_islands),
        patch.object(builder, "build_page_islands", side_effect=compile_islands),
        patch.object(builder, "setup_prebuilt_assets"),
        patch("pilot.core.app.prebuilt_assets.PrebuiltAssets") as prebuilt,
    ):
        prebuilt.return_value.install.return_value = True
        prebuilt.return_value.has_page_islands = False
        app.has_page_islands = True
        app.has_studio_apps = False
        app.is_linked = False
        app.has_source_changes = route != "prebuilt"
        for generation in ("one", "two"):
            if route == "whole-bench":
                builder.build_assets()
            else:
                builder.build_assets_for_app(app)
            for filename in names(generation):
                assert (fallback / filename).read_text() == generation
            if route != "whole-bench":
                assert unrelated.read_text() == "old desk"

    assert not any(
        (fallback / filename).exists() or (islands / filename).exists() for filename in names("one")
    )


@pytest.mark.parametrize("corrupt", ["{", "[]", '{"invalid":null,"also-invalid":7}'])
def test_corrupt_local_manifests_do_not_block_builds_or_rewrites(tmp_path: Path, corrupt: str) -> None:
    builder = make_builder()
    builder.bench.sites_path = tmp_path / "sites"
    builder.bench.config.production.enabled = True
    app = make_app(tmp_path / "apps" / "gameplan")
    public = app.path / "gameplan" / "public"
    app.has_source_changes = True
    assets = builder.bench.sites_path / "assets"
    assets.mkdir(parents=True)
    (assets / "assets.json").write_text(corrupt)
    rtl_url = "/assets/gameplan/dist/css-rtl/desk.AAAAAAAA.css"
    (assets / "assets-rtl.json").write_text(json.dumps({"rtl_desk.bundle.css": rtl_url}))
    rtl_file = public / "dist/css-rtl/desk.AAAAAAAA.css"
    rtl_file.parent.mkdir(parents=True)
    rtl_file.write_text("rtl")

    def compile_assets(app):
        assert json.loads((assets / "assets.json").read_text()) == {}

    with (
        patch.object(builder, "prepare_asset_fallback"),
        patch.object(builder, "build_app_from_source", side_effect=compile_assets),
    ):
        builder.build_assets_for_app(app)

    assert (tmp_path / ".previous-bundles" / rtl_url.lstrip("/")).read_text() == "rtl"


def test_a_production_build_requires_a_working_nginx_fallback(tmp_path: Path) -> None:
    from pilot.exceptions import CommandError

    builder = make_builder()
    builder.bench.sites_path = tmp_path / "sites"
    builder.bench.config.production.enabled = True
    app = make_app(tmp_path / "apps" / "gameplan")
    app.has_source_changes = True

    with (
        patch("pilot.managers.nginx.NginxManager.prepare_asset_fallback") as load_fallback,
        patch.object(builder, "build_app_from_source") as compile_assets,
    ):
        load_fallback.side_effect = CommandError("nginx reload failed")
        with pytest.raises(CommandError, match="nginx reload failed"):
            builder.build_assets_for_app(app)
        compile_assets.assert_not_called()


def test_retry_keeps_the_manifest_generations_island_dependencies(tmp_path: Path) -> None:
    builder = make_builder()
    builder.bench.sites_path = tmp_path / "sites"
    builder.bench.config.production.enabled = True
    app = make_app(tmp_path / "apps" / "game-plan", "game-plan")
    app.has_source_changes = True
    relative = Path("dist/island")
    public = app.path / app.module_name / "public"
    islands = public / relative
    fallback = tmp_path / ".previous-bundles" / "assets" / app.module_name / relative
    manifest = builder.bench.sites_path / "assets" / "assets.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"board.island.js": f"/assets/{app.module_name}/{relative}/board.js"}))
    files = ("board.js", "chunks/shared.js", "media/icon.svg")
    for filename in files:
        path = islands / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(filename)

    def interrupt_build(app):
        shutil.rmtree(islands)
        raise BenchError("build failed")

    with (
        patch.object(builder, "prepare_asset_fallback"),
        patch.object(builder, "build_app_from_source", side_effect=interrupt_build),
        pytest.raises(BenchError, match="build failed"),
    ):
        builder.build_assets_for_app(app)

    # A partially published manifest can reference both recovered and live islands.
    islands.mkdir(parents=True, exist_ok=True)
    (islands / "live.js").write_text("live")
    (islands / "live-chunk.js").write_text("chunk")
    entries = json.loads(manifest.read_text())
    entries["live.island.js"] = f"/assets/{app.module_name}/{relative}/live.js"
    manifest.write_text(json.dumps(entries))

    def complete_build(app):
        assert (fallback / "live-chunk.js").read_text() == "chunk"
        for filename in files:
            assert (fallback / filename).read_text() == filename
        shutil.rmtree(islands, ignore_errors=True)
        islands.mkdir(parents=True)
        (islands / "new.js").write_text("new")
        manifest.write_text(json.dumps({"board.island.js": f"/assets/{app.module_name}/{relative}/new.js"}))

    with (
        patch.object(builder, "prepare_asset_fallback"),
        patch.object(builder, "build_app_from_source", side_effect=complete_build),
    ):
        builder.build_assets_for_app(app)
    assert not (fallback.parents[2] / app.config.name).exists()


def test_failed_build_keeps_priority_over_partial_live_file_until_retry(tmp_path: Path) -> None:
    builder = make_builder()
    builder.bench.sites_path = tmp_path / "sites"
    builder.bench.config.production.enabled = True
    app = make_app(tmp_path / "apps/gameplan")
    url = "/assets/gameplan/dist/js/desk.bundle.SAME0000.js"
    live = app.path / "gameplan/public/dist/js/desk.bundle.SAME0000.js"
    live.parent.mkdir(parents=True)
    live.write_bytes(b"complete")
    assets = builder.bench.sites_path / "assets"
    assets.mkdir(parents=True)
    (assets / "assets.json").write_text(json.dumps({"desk.bundle.js": url}))
    priority = tmp_path / ".previous-bundles/building" / url.lstrip("/")
    snapshot = tmp_path / ".previous-bundles" / url.lstrip("/")
    with (
        pytest.raises(BenchError, match="interrupted"),
        builder.keeping_previous_bundles("gameplan", app.path),
    ):
        live.write_bytes(b"")
        raise BenchError("interrupted")

    assert priority.read_bytes() == b"complete"
    with builder.keeping_previous_bundles("gameplan", app.path):
        assert priority.read_bytes() == snapshot.read_bytes() == b"complete"
        live.write_bytes(b"rebuilt")
    assert not priority.exists()
    assert live.read_bytes() == b"rebuilt"


def test_second_build_can_publish_snapshots_without_directory_exchange(tmp_path: Path) -> None:
    import errno

    builder = make_builder()
    builder.bench.sites_path = tmp_path / "sites"
    builder.bench.config.production.enabled = True
    app = make_app(tmp_path / "apps/gameplan")
    live = app.path / "gameplan/public/dist/js/desk.bundle.AAAAAAAA.js"
    live.parent.mkdir(parents=True)
    live.write_text("original")
    with builder.keeping_previous_bundles("gameplan", app.path):
        live.write_text("next")

    with (
        patch(
            "pilot.managers.python_assets.exchange_paths", side_effect=OSError(errno.ENOTSUP, "unsupported")
        ),
        builder.keeping_previous_bundles("gameplan", app.path),
    ):
        fallback = tmp_path / ".previous-bundles/assets/gameplan/dist/js" / live.name
        assert fallback.read_text() == "next"


VALID_MANIFEST = '{"desk.bundle.js": "/assets/frappe/dist/js/desk.js"}'


@pytest.mark.parametrize(
    ("primary", "rtl", "is_other_app_built", "rebuilds_every_app"),
    [
        (None, None, True, True),
        ("{", None, True, True),
        (None, None, False, False),
        (VALID_MANIFEST, None, True, False),
        (VALID_MANIFEST, "{}", True, False),
        (VALID_MANIFEST, "{", True, True),
        (VALID_MANIFEST, "[]", True, True),
    ],
)
def test_an_unusable_manifest_rebuilds_every_app_only_when_an_unselected_app_is_built(
    tmp_path: Path, primary: str | None, rtl: str | None, is_other_app_built: bool, rebuilds_every_app: bool
) -> None:
    """The RTL map is optional, and the selected app's own build never widens the rebuild."""
    builder = make_builder()
    builder.bench.sites_path = tmp_path / "sites"
    assets = builder.bench.sites_path / "assets"
    assets.mkdir(parents=True)
    for name, content in (("assets.json", primary), ("assets-rtl.json", rtl)):
        if content is not None:
            (assets / name).write_text(content)
    selected = make_app(tmp_path / "apps/gameplan")
    (selected.path / "gameplan/public/dist").mkdir(parents=True)
    other = make_app(tmp_path / "apps/frappe", "frappe")
    if is_other_app_built:
        (other.path / "frappe/public/dist").mkdir(parents=True)
    # The bench makes new app objects on every call, so the selected app is matched by name.
    builder.bench.apps.return_value = [make_app(selected.path), other]

    with (
        patch.object(builder, "build_all_locked") as full_build,
        patch.object(builder, "build_app_assets") as single_build,
    ):
        builder.build_assets_for_app(selected)

    assert full_build.called is rebuilds_every_app
    assert single_build.called is not rebuilds_every_app


def test_multi_app_batch_keeps_pre_update_frappe_islands(tmp_path: Path) -> None:
    builder = make_builder()
    builder.bench.config.production.enabled = True
    builder.bench.sites_path = tmp_path / "sites"
    builder.bench.apps_path = tmp_path / "apps"
    frappe = make_app(builder.bench.apps_path / "frappe", "frappe")
    erpnext = make_app(builder.bench.apps_path / "erpnext", "erpnext")
    builder.bench.apps.return_value = [frappe, erpnext]
    directory = Path("dist/page-island")
    live = frappe.path / "frappe/public" / directory
    live.mkdir(parents=True)
    old = live / "board.OLD.js"
    old.write_text("old")
    assets = builder.bench.sites_path / "assets"
    assets.mkdir(parents=True)
    (assets / "assets.json").write_text(json.dumps({"board": f"/assets/frappe/{directory}/{old.name}"}))
    fallback = tmp_path / ".previous-bundles/assets/frappe" / directory
    for app in (frappe, erpnext):
        app.has_source_changes = True

    def compile_app(app):
        shutil.rmtree(live)
        live.mkdir()
        (live / f"{app.module_name}.js").write_text(app.module_name)
        assert (fallback / old.name).read_text() == "old"

    with (
        patch.object(builder, "prepare_asset_fallback"),
        patch("pilot.managers.python_assets.NodePackages"),
        patch.object(builder, "build_app_from_source", side_effect=compile_app),
        builder.asset_build_batch([frappe, erpnext]) as needs_build,
    ):
        assert needs_build
        for app in (frappe, erpnext):
            builder.build_app_assets(app)

    assert (fallback / old.name).read_text() == "old"
