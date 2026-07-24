from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import pilot
from admin.backend import frontend
from pilot.exceptions import BenchError


def _layout(root: Path, *, source: bool, dist: bool) -> None:
    if source:
        pkg = root / "admin" / "frontend" / "package.json"
        pkg.parent.mkdir(parents=True, exist_ok=True)
        pkg.write_text("{}")
    if dist:
        assets = root / "admin" / "backend" / "static" / "dist" / "assets"
        assets.mkdir(parents=True, exist_ok=True)


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


def test_released_install_without_dist_raises(tmp_path: Path) -> None:
    _layout(tmp_path, source=True, dist=False)
    with (
        patch.object(pilot, "is_dev_build", False),
        patch("pilot.utils.cli_root", return_value=tmp_path),
        pytest.raises(BenchError, match="missing from this release"),
    ):
        frontend.ensure_admin_frontend()
