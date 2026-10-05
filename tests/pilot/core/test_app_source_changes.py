from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pilot.config import AppConfig
from pilot.core.app import App
from pilot.internal.git import GitRepo
from tests.pilot.managers.test_managers_extra import make_bench

_PYPROJECT = """[tool.bench.assets]
build_dir = "./frontend"
out_dir = "../crm/public/frontend"
index_html_path = "../crm/www/crm.html"
"""


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t", *args], check=True)


@pytest.fixture
def app(tmp_path: Path) -> App:
    """A committed app with a SPA declared the Vite way, relative to its build_dir."""
    bench = make_bench(tmp_path)
    path = bench.apps_path / "crm"
    files = {
        "pyproject.toml": _PYPROJECT,
        "crm/www/crm.html": "<html>",
        "crm/public/frontend/index.js": "built",
        "frontend/components.d.ts": "declare",
        "frontend/src/App.vue": "<template/>",
        "yarn.lock": "lock",
    }
    for name, content in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(content)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return App(AppConfig(name="crm", repo="https://github.com/frappe/crm", branch="develop"), bench)


def test_a_clean_checkout_has_no_source_changes(app: App) -> None:
    assert app.has_source_changes is False


def test_files_the_asset_build_rewrites_do_not_count(app: App) -> None:
    for name in ("crm/www/crm.html", "crm/public/frontend/index.js", "frontend/components.d.ts"):
        (app.path / name).write_text("rebuilt")
    (app.path / "crm/public/frontend/new-chunk.js").write_text("new")

    assert app.has_source_changes is False


@pytest.mark.parametrize("name", ["frontend/src/App.vue", "yarn.lock"])
def test_an_edit_outside_the_build_outputs_counts(app: App, name: str) -> None:
    # A changed lockfile is a dependency change the published assets did not build with.
    (app.path / name).write_text("edited")

    assert app.has_source_changes is True


def test_a_rename_lists_only_its_new_path(app: App) -> None:
    _git(app.path, "mv", "frontend/src/App.vue", "frontend/src/Main.vue")

    assert GitRepo(app.path).changed_paths == ["frontend/src/Main.vue"]


def test_an_app_with_a_frappe_ui_page_has_page_islands(app: App) -> None:
    assert app.has_page_islands is False

    page = app.path / "crm" / "crm" / "page" / "board"
    page.mkdir(parents=True)
    (page / "board.island.js").write_text("export default {}")

    assert app.has_page_islands is True
