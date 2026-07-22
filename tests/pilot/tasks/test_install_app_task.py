"""Tests for InstallAppTask."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.core.site import Site
from pilot.exceptions import CommandError
from pilot.tasks.install_app import InstallAppTask
from tests.pilot.commands.test_commands import make_bench


def make_app_dir(bench, name: str, required_apps: list[str] | None = None) -> None:
    module_dir = bench.apps_path / name / name
    module_dir.mkdir(parents=True)
    hooks = f"required_apps = {required_apps!r}\n" if required_apps else ""
    (module_dir / "hooks.py").write_text(hooks)


def make_task(bench_root: Path, site: str, app: str) -> InstallAppTask:
    bench = make_bench(bench_root)
    bench.create_directories()
    return InstallAppTask(bench=bench, bench_root=bench_root, site=site, app=app)


def test_install_app_task_uses_site_install_app(tmp_path: Path) -> None:
    task = make_task(tmp_path, "site1.localhost", "helpdesk")
    make_app_dir(task.bench, "helpdesk")

    with (
        patch.object(Site, "install_app") as mock_install,
        patch("pilot.managers.environment.PythonEnvManager.build_assets_for_app"),
    ):
        task.run()

    mock_install.assert_called_once()
    (installed_app,) = mock_install.call_args.args
    assert installed_app.config.name == "helpdesk"


def test_install_app_task_builds_assets_for_app_and_required_apps(tmp_path: Path) -> None:
    task = make_task(tmp_path, "site1.localhost", "helpdesk")
    make_app_dir(task.bench, "helpdesk", required_apps=["telephony"])
    make_app_dir(task.bench, "telephony")

    with (
        patch.object(Site, "install_app"),
        patch("pilot.managers.environment.PythonEnvManager.build_assets_for_app") as mock_build,
    ):
        task.run()

    built = [call.args[0].config.name for call in mock_build.call_args_list]
    assert built == ["helpdesk", "telephony"]


def test_install_app_task_skips_required_app_missing_from_bench(tmp_path: Path) -> None:
    task = make_task(tmp_path, "site1.localhost", "helpdesk")
    make_app_dir(task.bench, "helpdesk", required_apps=["not_on_bench"])

    with (
        patch.object(Site, "install_app"),
        patch("pilot.managers.environment.PythonEnvManager.build_assets_for_app") as mock_build,
    ):
        task.run()

    built = [call.args[0].config.name for call in mock_build.call_args_list]
    assert built == ["helpdesk"]


def test_install_app_task_exits_nonzero_when_site_install_fails(tmp_path: Path) -> None:
    task = make_task(tmp_path, "site1.localhost", "helpdesk")
    make_app_dir(task.bench, "helpdesk")

    with (
        patch.object(Site, "install_app", side_effect=CommandError("boom")),
        pytest.raises(SystemExit) as exc,
    ):
        task.run()

    assert exc.value.code == 1
