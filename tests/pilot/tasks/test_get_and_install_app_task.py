"""Tests for GetAndInstallAppTask."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.core.site import Site
from pilot.tasks.get_and_install_app import GetAndInstallAppTask
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


def test_run_installs_the_fetched_app_on_sites(tmp_path: Path) -> None:
    """App.install already builds assets, so run() must not build them again."""
    task = make_task(tmp_path, ["site1.localhost"])

    fake_result = MagicMock()
    fake_result.app.config.name = "helpdesk"

    with (
        patch.object(task, "fetch", return_value=fake_result),
        patch.object(task, "install_on_sites") as mock_install_on_sites,
        patch("pilot.managers.environment.PythonEnvManager.build_assets_for_app") as mock_build,
    ):
        task.run()

    mock_install_on_sites.assert_called_once_with(fake_result.app)
    mock_build.assert_not_called()


def test_a_failed_worker_reload_stops_before_installing_on_sites(tmp_path):
    from pilot.exceptions import BenchError

    task = make_task(tmp_path, ["site1.localhost"])
    with (
        patch.object(task, "fetch", return_value=MagicMock()),
        patch.object(task, "install_on_sites") as install,
        patch("pilot.managers.processes.local.ProcessManager.for_bench") as manager,
    ):
        manager.return_value.reload_workers.side_effect = BenchError("restart failed")
        with pytest.raises(BenchError, match="restart failed"):
            task.run()
    install.assert_not_called()
