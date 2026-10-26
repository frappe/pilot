from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import pilot
from admin.backend import frontend
from pilot.exceptions import BenchError


def _layout(root: Path, *, source: bool, dist: bool) -> None:
    if source:
        pkg = root / "admin" / "frontend" / "dashboard" / "package.json"
        pkg.parent.mkdir(parents=True, exist_ok=True)
        pkg.write_text("{}")
    if dist:
        assets = root / "admin" / "backend" / "static" / "dashboard" / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        (assets.parent / "index.html").write_text("<!doctype html>")


def write_build_output(argv, **kwargs) -> None:
    if argv[:3] != ["npm", "run", "build"]:
        return
    staged = Path(argv[-1])
    (staged / "assets").mkdir(parents=True)
    (staged / "assets" / "new.js").write_text("new asset")
    (staged / "index.html").write_text("new index")


def _ensure(root: Path, *, is_dev: bool) -> object:
    with (
        patch.object(pilot, "is_dev_build", is_dev),
        patch("pilot.utils.cli_root", return_value=root),
        patch.object(frontend, "build_admin_frontend") as build,
    ):
        frontend.ensure_admin_frontend()
    return build


def test_released_install_serves_dist_without_building(tmp_path: Path) -> None:
    _layout(tmp_path, source=True, dist=True)
    build = _ensure(tmp_path, is_dev=False)
    build.assert_not_called()


def test_dev_build_compiles_from_source(tmp_path: Path) -> None:
    _layout(tmp_path, source=True, dist=True)
    build = _ensure(tmp_path, is_dev=True)
    build.assert_called_once()


def test_build_frontend_skips_when_dist_already_exists(tmp_path: Path) -> None:
    frontend_dir = tmp_path / "frontend"
    dist_dir = tmp_path / "dist"
    frontend_dir.mkdir(parents=True)
    (dist_dir / "assets").mkdir(parents=True)
    (dist_dir / "index.html").write_text("old index")

    with patch("pilot.utils.run_command") as run_command:
        frontend._build_frontend(frontend_dir, dist_dir, "dashboard", lambda message: None)
    run_command.assert_not_called()


def test_build_frontend_skips_when_in_app_embed_iife_exists(tmp_path: Path) -> None:
    frontend_dir = tmp_path / "frontend"
    dist_dir = tmp_path / "dist"
    frontend_dir.mkdir(parents=True)
    dist_dir.mkdir(parents=True)
    (dist_dir / "cloud-settings.js").write_text("// iife")

    with patch("pilot.utils.run_command") as run_command:
        frontend._build_frontend(frontend_dir, dist_dir, "in-app-embed", lambda message: None)
    run_command.assert_not_called()


def test_build_frontend_builds_when_dist_is_missing(tmp_path: Path) -> None:
    frontend_dir = tmp_path / "frontend"
    dist_dir = tmp_path / "dist"
    frontend_dir.mkdir(parents=True)

    with patch("pilot.utils.run_command", side_effect=write_build_output) as run_command:
        frontend._build_frontend(frontend_dir, dist_dir, "dashboard", lambda message: None)
    command = run_command.call_args.args[0]
    assert command[:5] == ["npm", "run", "build", "--", "--outDir"]
    assert Path(command[-1]) != dist_dir
    assert (dist_dir / "index.html").read_text() == "new index"


def test_build_frontend_forces_rebuild_even_when_dist_exists(tmp_path: Path) -> None:
    frontend_dir = tmp_path / "frontend"
    dist_dir = tmp_path / "dist"
    frontend_dir.mkdir(parents=True)
    (dist_dir / "assets").mkdir(parents=True)
    (dist_dir / "index.html").write_text("old index")
    (dist_dir / "assets" / "old.js").write_text("old asset")

    with patch("pilot.utils.run_command", side_effect=write_build_output):
        frontend._build_frontend(frontend_dir, dist_dir, "dashboard", lambda message: None, force=True)
    assert (dist_dir / "index.html").read_text() == "new index"
    assert (dist_dir / "assets" / "old.js").read_text() == "old asset"
    assert (dist_dir / "assets" / "new.js").read_text() == "new asset"


def test_incomplete_build_keeps_the_served_frontend(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    (dist_dir / "assets").mkdir(parents=True)
    (dist_dir / "index.html").write_text("old index")
    (dist_dir / "assets" / "old.js").write_text("old asset")
    with patch("pilot.utils.run_command"), pytest.raises(BenchError, match="complete frontend"):
        frontend._build_frontend(tmp_path, dist_dir, "dashboard", lambda message: None, force=True)
    assert (dist_dir / "index.html").read_text() == "old index"
    assert (dist_dir / "assets" / "old.js").read_text() == "old asset"


def test_released_install_without_dist_raises(tmp_path: Path) -> None:
    _layout(tmp_path, source=True, dist=False)
    with (
        patch.object(pilot, "is_dev_build", False),
        patch("pilot.utils.cli_root", return_value=tmp_path),
        pytest.raises(BenchError, match="missing from this release"),
    ):
        frontend.ensure_admin_frontend()
