"""Tests for pilot.tasks.jobs.get_and_install_app_task.GetAndInstallAppTask.

Only the requested app itself gets installed on sites — Frappe's own
`site install-app` already cascades installing declared dependencies onto
the site. But it never builds assets for anything, so this task still
builds assets for the app *and* every dependency itself.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from pilot.tasks.jobs.get_and_install_app_task import GetAndInstallAppTask
from pilot.core.site import Site
from tests.pilot.commands.test_commands import make_bench


def make_task(bench_root: Path, sites: list[str]) -> GetAndInstallAppTask:
    args = MagicMock(repo="https://github.com/frappe/helpdesk", branch="", marketplace_app="", sites=sites)
    bench = make_bench(bench_root)
    bench.create_directories()
    return GetAndInstallAppTask(bench, bench_root, args)


def test_install_on_sites_only_installs_the_given_app(tmp_path: Path) -> None:
    task = make_task(tmp_path, ["site1.localhost"])
    app = MagicMock()
    app.config.name = "helpdesk"

    with patch.object(Site, "install_app") as mock_install:
        task._install_on_sites(app)

    mock_install.assert_called_once_with(app)


def test_build_assets_builds_for_app_and_every_dependency(tmp_path: Path) -> None:
    task = make_task(tmp_path, [])
    app = MagicMock()
    app.config.name = "helpdesk"
    dep = MagicMock()
    dep.config.name = "telephony"

    with patch("pilot.managers.python_environment.PythonEnvManager.build_assets_for_app") as mock_build:
        task._build_assets([app, dep])

    assert mock_build.call_args_list == [((app,),), ((dep,),)]


def test_run_installs_only_app_on_sites_but_builds_assets_for_dependencies_too(tmp_path: Path) -> None:
    """Regression: install-app cascades a dependency onto the site, but never
    builds its assets — run() must still build assets for
    cmd.installed_dependencies, even though they never reach _install_on_sites."""
    task = make_task(tmp_path, ["site1.localhost"])

    fake_cmd = MagicMock()
    fake_cmd.app.config.name = "helpdesk"
    fake_cmd.installed_dependencies = [MagicMock()]

    with patch.object(task, "_fetch", return_value=fake_cmd), \
            patch.object(task, "_install_on_sites") as mock_install_on_sites, \
            patch.object(task, "_build_assets") as mock_build_assets:
        task.run()

    mock_install_on_sites.assert_called_once_with(fake_cmd.app)
    mock_build_assets.assert_called_once_with([fake_cmd.app] + fake_cmd.installed_dependencies)
