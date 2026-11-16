"""Tests for BenchUpdater.update_apps validating apps after they move revision."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.core.app import App
from pilot.core.app.revisions import RevisionPin
from pilot.core.bench.update import BenchUpdater
from pilot.exceptions import AppValidationError
from tests.pilot.commands.test_commands import make_bench


def _write_app(bench, name: str, hooks: str = "app_name = 'x'\n", fixture: str | None = None) -> Path:
    app_path = bench.apps_path / name
    (app_path / name).mkdir(parents=True)
    (app_path / ".git").mkdir()
    (app_path / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\n\n'
        '[tool.bench.frappe-dependencies]\nfrappe = ">=16.0.0,<17.0.0"\n'
    )
    (app_path / name / "__init__.py").write_text("")
    (app_path / name / "hooks.py").write_text(hooks)
    if fixture is not None:
        (app_path / name / "fixtures").mkdir()
        (app_path / name / "fixtures" / "role.json").write_text(fixture)
    return app_path


def _pins(*names: str) -> dict[str, RevisionPin]:
    """A revision to move each app to. Without one an app is left where it is,
    and an app that never moved has nothing to re-validate."""
    return {name: RevisionPin(kind="commit", ref="abc1234") for name in names}


def test_update_apps_validates_every_updated_app(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, "myapp", fixture='[{"doctype": "Role"}]\n')
    _write_app(bench, "otherapp")

    with patch.object(App, "update") as mock_update:
        BenchUpdater(bench).update_apps(None, lambda message: None, pins=_pins("myapp", "otherapp"))

    assert mock_update.call_count == 2


def test_update_apps_rejects_a_corrupt_fixture_before_installing(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, "myapp", fixture='[{"doctype":\n')

    with (
        patch.object(App, "update"),
        pytest.raises(AppValidationError, match=r"fixtures/role\.json"),
    ):
        BenchUpdater(bench).update_apps(None, lambda message: None, pins=_pins("myapp"))


def test_update_apps_rejects_a_hook_pointing_at_deleted_code(tmp_path: Path) -> None:
    """The common update failure: the new revision renamed a hooked function."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, "myapp", hooks='after_migrate = "myapp.setup.after_migrate"\n')

    with (
        patch.object(App, "update"),
        pytest.raises(AppValidationError, match="has no 'setup'"),
    ):
        BenchUpdater(bench).update_apps(None, lambda message: None, pins=_pins("myapp"))


def test_update_apps_only_validates_the_filtered_apps(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, "myapp")
    _write_app(bench, "brokenapp", fixture="{oops\n")

    with patch.object(App, "update"):
        BenchUpdater(bench).update_apps({"myapp"}, lambda message: None, pins=_pins("myapp", "brokenapp"))


def test_update_apps_rejects_an_app_with_no_pyproject(tmp_path: Path) -> None:
    """A setup.py-only app is held to the same rules as a new one: pyproject.toml
    is mandatory, and an update is where a bench finds out it is missing."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    app_path = bench.apps_path / "oldapp"
    app_path.mkdir(parents=True)
    (app_path / ".git").mkdir()
    (app_path / "setup.py").write_text("from setuptools import setup; setup()\n")

    with (
        patch.object(App, "update"),
        pytest.raises(AppValidationError, match=r"pyproject\.toml"),
    ):
        BenchUpdater(bench).update_apps(None, lambda message: None, pins=_pins("oldapp"))


def test_update_apps_leaves_alone_an_app_that_is_not_moving(tmp_path: Path) -> None:
    """An app with no pin stays on its revision, so there is nothing to re-check -
    a defect it already had must not block an update of the apps around it."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, "myapp")
    _write_app(bench, "brokenapp", fixture="{oops\n")

    with patch.object(App, "update") as mock_update:
        BenchUpdater(bench).update_apps(None, lambda message: None, pins=_pins("myapp"))

    assert mock_update.call_count == 1


def test_update_validates_the_new_revision_against_every_check(tmp_path: Path) -> None:
    """A revision can drop a pyproject.toml or a declaration as easily as it can
    break a hook, so an update is held to the same standard as an install."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, "myapp")

    with patch.object(App, "update"), patch.object(App, "validate") as mock_validate:
        BenchUpdater(bench).update_apps(None, lambda message: None, pins=_pins("myapp"))

    mock_validate.assert_called_once()


def test_update_apps_reports_each_app_through_validation(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, "myapp")
    _write_app(bench, "otherapp")
    events: list[tuple[str, str]] = []

    with patch.object(App, "update"):
        BenchUpdater(bench).update_apps(
            None,
            lambda message: None,
            pins=_pins("myapp"),
            on_app=lambda name, status: events.append((name, status)),
        )

    assert ("myapp", "running") in events
    assert ("myapp", "success") in events
    assert ("otherapp", "skipped") in events


def test_update_apps_reports_the_app_that_failed_validation(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    _write_app(bench, "myapp", fixture='[{"doctype":\n')
    events: list[tuple[str, str]] = []

    with patch.object(App, "update"), pytest.raises(AppValidationError):
        BenchUpdater(bench).update_apps(
            None,
            lambda message: None,
            pins=_pins("myapp"),
            on_app=lambda name, status: events.append((name, status)),
        )

    assert events[-1] == ("myapp", "failed")
