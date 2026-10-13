"""Tests for PythonAssetBuilder."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.exceptions import BenchError
from pilot.internal.atomic_file import exclusive_file_lock
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


def test_asset_links_are_updated_under_one_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = MagicMock()
    manager.bench.sites_path = tmp_path / "sites"
    builder = PythonAssetBuilder(manager)
    public_dir = tmp_path / "apps" / "gameplan" / "gameplan" / "public"
    public_dir.mkdir(parents=True)
    node_modules = tmp_path / "apps" / "gameplan" / "node_modules"
    node_modules.mkdir()
    assets_dir = manager.bench.sites_path / "assets"
    old_link = assets_dir / "gameplan"
    old_link.mkdir(parents=True)
    lock_path = assets_dir / "asset-links"
    original_symlink_to = Path.symlink_to
    original_replace = os.replace
    linked_paths = []

    def symlink_under_lock(path: Path, target: Path, target_is_directory: bool = False) -> None:
        with pytest.raises(BlockingIOError), exclusive_file_lock(lock_path, blocking=False):
            pass
        linked_paths.append(path)
        original_symlink_to(path, target, target_is_directory=target_is_directory)

    def replace_under_lock(source: Path, target: Path) -> None:
        with pytest.raises(BlockingIOError), exclusive_file_lock(lock_path, blocking=False):
            pass
        assert target == old_link
        original_replace(source, target)

    monkeypatch.setattr(Path, "symlink_to", symlink_under_lock)
    monkeypatch.setattr(os, "replace", replace_under_lock)
    builder.link_app_assets("gameplan", public_dir)

    assert len(linked_paths) == 2
    assert linked_paths[0].parent.parent == assets_dir
    assert linked_paths[1] == public_dir / "node_modules"
    assert old_link.resolve() == public_dir
    assert (public_dir / "node_modules").resolve() == node_modules


def test_link_survives_frappe_removing_it_during_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = MagicMock()
    manager.bench.sites_path = tmp_path / "sites"
    builder = PythonAssetBuilder(manager)
    public_dir = tmp_path / "apps" / "gameplan" / "gameplan" / "public"
    public_dir.mkdir(parents=True)
    app_link = manager.bench.sites_path / "assets" / "gameplan"
    app_link.parent.mkdir(parents=True)
    app_link.symlink_to(public_dir)
    original_is_symlink = Path.is_symlink
    removed_by_frappe = False

    def remove_link_during_check(path: Path) -> bool:
        nonlocal removed_by_frappe
        is_symlink = original_is_symlink(path)
        if path == app_link and is_symlink and not removed_by_frappe:
            path.unlink()
            removed_by_frappe = True
        return is_symlink

    monkeypatch.setattr(Path, "is_symlink", remove_link_during_check)
    builder.link_app_assets("gameplan", public_dir)

    assert removed_by_frappe
    assert app_link.is_symlink()
    assert app_link.resolve() == public_dir


@pytest.mark.parametrize(
    ("source_extension", "output_dir", "output_extension"),
    [("ts", "js", "js"), ("scss", "css", "css"), ("jsx", "", "js")],
)
def test_linked_bundle_requires_its_manifest_and_output(
    tmp_path: Path, source_extension: str, output_dir: str, output_extension: str
) -> None:
    manager = MagicMock()
    manager.bench.sites_path = tmp_path / "sites"
    builder = PythonAssetBuilder(manager)
    app = make_app(tmp_path / "gameplan")
    public = app.path / "gameplan" / "public"
    source_dir = public / "js" / "nested"
    source_dir.mkdir(parents=True)
    (source_dir / f"desk.bundle.{source_extension}").write_text("source")
    assets = manager.bench.sites_path / "assets"
    assets.mkdir(parents=True)
    (assets / "gameplan").symlink_to(public)

    assert not builder.has_app_assets(app)

    dist = public / "dist" / output_dir
    dist.mkdir(parents=True)
    output = dist / f"desk.bundle.AAAAAAAA.{output_extension}"
    output.write_text("built")
    manifest = assets / "assets.json"
    url = f"/assets/gameplan/dist/{output_dir}/desk.bundle.AAAAAAAA.{output_extension}"
    if not output_dir:
        url = f"/assets/gameplan/dist/desk.bundle.AAAAAAAA.{output_extension}"
    key = (
        f"desk.bundle.{output_extension}" if output_extension == "css" else f"desk.bundle.{source_extension}"
    )
    manifest.write_text(json.dumps({key: url}))
    assert builder.has_app_assets(app)

    manifest.write_text("[]")
    assert not builder.has_app_assets(app)
    manifest.write_text(json.dumps({key: url}))

    output.unlink()
    assert not builder.has_app_assets(app)


def test_linked_app_requires_declared_frontend_outputs(tmp_path: Path) -> None:
    manager = MagicMock()
    manager.bench.sites_path = tmp_path / "sites"
    builder = PythonAssetBuilder(manager)
    app = make_app(tmp_path / "gameplan")
    write_package(app.path / "frontend", scripts={"build": "vite build"})
    public = app.path / "gameplan" / "public"
    public.mkdir(parents=True)
    assets = manager.bench.sites_path / "assets"
    assets.mkdir(parents=True)
    (assets / "gameplan").symlink_to(public)

    assert not builder.has_app_assets(app)

    (app.path / "pyproject.toml").write_text(
        '[tool.bench.assets]\nbuild_dir = "frontend"\nout_dir = "gameplan/public/frontend"\n'
        'index_html_path = "gameplan/www/g.html"\n'
    )
    assert not builder.has_app_assets(app)
    output = public / "frontend"
    output.mkdir()
    (output / "app.js").write_text("built")
    assert not builder.has_app_assets(app)
    html = app.path / "gameplan" / "www" / "g.html"
    html.parent.mkdir(parents=True)
    html.write_text("<html></html>")
    assert builder.has_app_assets(app)


def test_linked_static_app_needs_no_generated_outputs(tmp_path: Path) -> None:
    manager = MagicMock()
    manager.bench.sites_path = tmp_path / "sites"
    builder = PythonAssetBuilder(manager)
    app = make_app(tmp_path / "static_app", "static_app")
    public = app.path / "static_app" / "public"
    public.mkdir(parents=True)
    (public / "logo.svg").write_text("<svg />")
    assets = manager.bench.sites_path / "assets"
    assets.mkdir(parents=True)
    (assets / "static_app").symlink_to(public)

    assert builder.has_app_assets(app)


def test_empty_prebuilt_archive_keeps_its_link_after_broken_link_cleanup(tmp_path: Path) -> None:
    manager = MagicMock()
    manager.bench.sites_path = tmp_path / "sites"
    builder = PythonAssetBuilder(manager)
    app = make_app(tmp_path / "empty_app", "empty_app")
    app.path.mkdir()
    public = app.path / "empty_app" / "public"
    assets = manager.bench.sites_path / "assets"
    assert not public.exists()
    assets.mkdir(parents=True)
    (assets / "empty_app").symlink_to(public)
    assert not builder.has_app_assets(app)

    builder.setup_prebuilt_assets("empty_app", public, public / "dist")

    for link in assets.iterdir():
        if link.is_symlink() and not link.exists():
            link.unlink()

    assert public.is_dir()
    assert (assets / "empty_app").is_symlink()
    assert builder.has_app_assets(app)


def test_legacy_prebuilt_jsx_archive_builds_to_write_its_manifest(tmp_path: Path) -> None:
    manager = MagicMock()
    manager.bench.sites_path = tmp_path / "sites"
    manager.bench.frappe_call = ["python"]
    builder = PythonAssetBuilder(manager)
    app = make_app(tmp_path / "raven", "raven")
    app.has_source_changes = False
    public = app.path / "raven" / "public"
    public.mkdir(parents=True)
    (public / "raven_chat.bundle.jsx").write_text("source")
    output = public / "dist" / "raven_chat.bundle.AAAAAAAA.js"
    output.parent.mkdir()
    output.write_text("prebuilt")

    def compile_assets(argv: list[str], **kwargs: object) -> None:
        manifest = manager.bench.sites_path / "assets" / "assets.json"
        manifest.write_text(
            json.dumps({"raven_chat.bundle.jsx": "/assets/raven/dist/raven_chat.bundle.AAAAAAAA.js"})
        )

    with (
        patch("pilot.core.app.prebuilt_assets.PrebuiltAssets.install", return_value=True),
        patch.object(builder, "run_compiler", side_effect=compile_assets) as compiler,
    ):
        builder.build_assets_for_app(app)

    compiler.assert_called_once()
    assert builder.has_app_assets(app)


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
