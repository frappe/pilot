"""Tests for App.editable_target: dev benches pull an app's `dev` extra."""

from __future__ import annotations

from pathlib import Path

from pilot.config import AppConfig
from pilot.core.app import App
from tests.pilot.commands.test_commands import make_bench


def _make_app(bench, name: str, pyproject: str | None) -> App:
    app = App(AppConfig(name=name, repo=f"https://example.com/{name}.git", branch="main"), bench)
    app.path.mkdir(parents=True)
    if pyproject is not None:
        (app.path / "pyproject.toml").write_text(pyproject)
    return app


_WITH_DEV_EXTRA = """
[project]
name = "frappe"

[project.optional-dependencies]
dev = ["watchdog~=6.0.0"]
"""

_WITHOUT_DEV_EXTRA = """
[project]
name = "myapp"
"""


def test_dev_bench_installs_the_dev_extra(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config.production.enabled = False
    app = _make_app(bench, "frappe", _WITH_DEV_EXTRA)

    assert app.has_dev_extra
    assert app.editable_target == f"{app.path}[dev]"


def test_production_bench_skips_the_dev_extra(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config.production.enabled = True
    app = _make_app(bench, "frappe", _WITH_DEV_EXTRA)

    assert app.editable_target == str(app.path)


def test_app_without_a_dev_extra_installs_plainly(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config.production.enabled = False
    app = _make_app(bench, "myapp", _WITHOUT_DEV_EXTRA)

    assert not app.has_dev_extra
    assert app.editable_target == str(app.path)


def test_missing_pyproject_installs_plainly(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config.production.enabled = False
    app = _make_app(bench, "legacy", None)

    assert not app.has_dev_extra
    assert app.editable_target == str(app.path)


def test_malformed_pyproject_installs_plainly(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config.production.enabled = False
    app = _make_app(bench, "broken", "[project\nname =")

    assert not app.has_dev_extra
    assert app.editable_target == str(app.path)
    assert app.module_name == "broken"
