"""Tests for AppProvider's best-effort pyproject.toml title/description scraping."""

from __future__ import annotations

from pathlib import Path

from admin.backend.providers.apps import AppProvider


def _make_app(bench_root: Path, name: str, pyproject: str | None = None) -> Path:
    app_path = bench_root / "apps" / name
    app_path.mkdir(parents=True)
    if pyproject is not None:
        (app_path / "pyproject.toml").write_text(pyproject)
    return app_path


def test_pyproject_meta_reads_name_and_description(tmp_path: Path) -> None:
    _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\ndescription = "A demo Frappe app"\n',
    )
    provider = AppProvider(tmp_path)
    title, description = provider.get_pyproject_meta(tmp_path / "apps" / "myapp", "myapp")
    assert title == "myapp"
    assert description == "A demo Frappe app"


def test_pyproject_meta_falls_back_to_folder_name_when_missing(tmp_path: Path) -> None:
    _make_app(tmp_path, "myapp")
    provider = AppProvider(tmp_path)
    title, description = provider.get_pyproject_meta(tmp_path / "apps" / "myapp", "myapp")
    assert title == "myapp"
    assert description == ""


def test_pyproject_meta_falls_back_on_unparseable_toml(tmp_path: Path) -> None:
    _make_app(tmp_path, "myapp", "not valid toml [[[")
    provider = AppProvider(tmp_path)
    title, description = provider.get_pyproject_meta(tmp_path / "apps" / "myapp", "myapp")
    assert title == "myapp"
    assert description == ""


def test_read_all_includes_title_and_description(tmp_path: Path) -> None:
    app_path = _make_app(
        tmp_path,
        "myapp",
        '[project]\nname = "myapp"\ndescription = "A demo Frappe app"\n',
    )
    (app_path / ".git").mkdir()
    apps = AppProvider(tmp_path).get_all()
    assert len(apps) == 1
    assert apps[0].title == "myapp"
    assert apps[0].description == "A demo Frappe app"
