"""Tests for App.editable_target: dev benches pull an app's `dev` extra."""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_install_dev_extra_false_skips_the_dev_extra(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config.production.enabled = False
    bench.config.install_dev_extra = False
    app = _make_app(bench, "frappe", _WITH_DEV_EXTRA)

    assert app.has_dev_extra
    assert app.editable_target == str(app.path)


def test_install_falls_back_without_the_dev_extra_when_it_does_not_resolve(tmp_path: Path) -> None:
    from unittest.mock import patch

    from pilot.exceptions import CommandError
    from pilot.managers.environment import PythonEnvManager

    bench = make_bench(tmp_path)
    bench.config.production.enabled = False
    app = _make_app(bench, "frappe", _WITH_DEV_EXTRA)

    with (
        patch("pilot.managers.environment.ensure_uv", return_value="uv"),
        patch(
            "pilot.managers.environment.run_command", side_effect=[CommandError("no solution"), None]
        ) as run,
    ):
        PythonEnvManager(bench).install_app(app)

    assert [call.args[0][-1] for call in run.call_args_list] == [f"{app.path}[dev]", str(app.path)]


def test_install_without_a_dev_extra_raises_the_install_error(tmp_path: Path) -> None:
    from unittest.mock import patch

    import pytest

    from pilot.exceptions import CommandError
    from pilot.managers.environment import PythonEnvManager

    bench = make_bench(tmp_path)
    app = _make_app(bench, "myapp", _WITHOUT_DEV_EXTRA)

    with (
        patch("pilot.managers.environment.ensure_uv", return_value="uv"),
        patch("pilot.managers.environment.run_command", side_effect=CommandError("broken")) as run,
        pytest.raises(CommandError, match="broken"),
    ):
        PythonEnvManager(bench).install_app(app)

    assert run.call_count == 1


def test_batch_install_without_dev_extras_raises_the_install_error(tmp_path: Path) -> None:
    from unittest.mock import patch

    from pilot.exceptions import CommandError
    from pilot.managers.environment import PythonEnvManager

    bench = make_bench(tmp_path)
    bench.config.production.enabled = False
    apps = [_make_app(bench, name, _WITHOUT_DEV_EXTRA) for name in ("frappe", "myapp")]

    with (
        patch("pilot.managers.environment.ensure_uv", return_value="uv"),
        patch("pilot.managers.environment.run_command", side_effect=CommandError("no solution")) as run,
        pytest.raises(CommandError, match="no solution"),
    ):
        PythonEnvManager(bench).install_apps(apps)

    assert run.call_count == 1


def test_batch_install_drops_only_the_dev_extra_that_does_not_resolve(tmp_path: Path) -> None:
    from unittest.mock import patch

    from pilot.exceptions import CommandError
    from pilot.managers.environment import PythonEnvManager

    bench = make_bench(tmp_path)
    bench.config.production.enabled = False
    broken = _make_app(bench, "frappe", _WITH_DEV_EXTRA)
    working = _make_app(bench, "myapp", _WITH_DEV_EXTRA)
    failure = CommandError("no solution")

    with (
        patch("pilot.managers.environment.ensure_uv", return_value="uv"),
        patch("pilot.managers.environment.run_command", side_effect=[failure, failure, None, None]) as run,
    ):
        PythonEnvManager(bench).install_apps([broken, working])

    installed = [
        [argv[index + 1] for index, value in enumerate(argv) if value == "-e"]
        for argv in (call.args[0] for call in run.call_args_list)
    ]
    assert installed == [
        [broken.editable_target, working.editable_target],
        [broken.editable_target],
        [str(broken.path)],
        [working.editable_target],
    ]
