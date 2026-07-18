"""Tests for GetAndInstallAppTask."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from pilot.tasks.get_and_install_app import GetAndInstallAppTask
from pilot.core.site import Site
from tests.pilot.commands.test_commands import make_bench


def make_task(bench_root: Path, sites: list[str]) -> GetAndInstallAppTask:
    bench = make_bench(bench_root)
    bench.create_directories()
    return GetAndInstallAppTask(
        bench=bench,
        bench_root=bench_root,
        repo="https://github.com/frappe/helpdesk",
        branch="",
        marketplace_app="",
        sites=sites,
    )


def test_site_alias_populates_sites_when_sites_is_omitted(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)

    task = GetAndInstallAppTask(
        bench=bench,
        bench_root=tmp_path,
        repo="https://github.com/frappe/helpdesk",
        site="site1.localhost",
    )

    assert task.sites == ["site1.localhost"]


def test_install_on_sites_only_installs_the_given_app(tmp_path: Path) -> None:
    task = make_task(tmp_path, ["site1.localhost"])
    app = MagicMock()
    app.config.name = "helpdesk"

    with patch.object(Site, "install_app") as mock_install:
        task.install_on_sites(app)

    mock_install.assert_called_once_with(app)


def test_build_assets_builds_for_app_and_every_dependency(tmp_path: Path) -> None:
    task = make_task(tmp_path, [])
    app = MagicMock()
    app.config.name = "helpdesk"
    dep = MagicMock()
    dep.config.name = "telephony"

    with patch(
        "pilot.managers.environment.PythonEnvManager.build_assets_for_app"
    ) as mock_build:
        task.build_assets([app, dep])

    assert mock_build.call_args_list == [((app,),), ((dep,),)]


def test_run_installs_only_app_on_sites_but_builds_assets_for_dependencies_too(
    tmp_path: Path,
) -> None:
    """run() builds assets for dependencies cascaded by install-app."""
    task = make_task(tmp_path, ["site1.localhost"])

    fake_cmd = MagicMock()
    fake_cmd.app.config.name = "helpdesk"
    fake_cmd.installed_dependencies = [MagicMock()]

    with (
        patch.object(task, "fetch", return_value=fake_cmd),
        patch.object(task, "install_on_sites") as mock_install_on_sites,
        patch.object(task, "build_assets") as mock_build_assets,
    ):
        task.run()

    mock_install_on_sites.assert_called_once_with(fake_cmd.app)
    mock_build_assets.assert_called_once_with([fake_cmd.app] + fake_cmd.installed_dependencies)
