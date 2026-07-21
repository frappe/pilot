"""Tests for CreateAppTask."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from pilot.tasks.create_app import CreateAppTask
from tests.pilot.commands.test_commands import make_bench


def test_create_app_task_defaults(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    task = CreateAppTask(
        bench=bench,
        bench_root=tmp_path,
        name="my_custom_app",
        title="My Custom App",
        sites=[],
    )

    assert task.name == "my_custom_app"
    assert task.title == "My Custom App"
    assert task.sites == []
    assert task.create_github_repo is False


def test_install_sites_skips_when_sites_empty(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    task = CreateAppTask(
        bench=bench,
        bench_root=tmp_path,
        name="my_custom_app",
        sites=[],
    )

    with patch.object(bench, "site") as mock_site:
        task.install_sites()

    mock_site.assert_not_called()


def test_install_sites_installs_on_all_provided_sites(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    task = CreateAppTask(
        bench=bench,
        bench_root=tmp_path,
        name="my_custom_app",
        sites=["site1.localhost", "site2.localhost"],
    )

    mock_site_obj = MagicMock()
    with patch.object(bench, "site", return_value=mock_site_obj) as mock_site_call:
        task.install_sites()

    assert mock_site_call.call_count == 2
    mock_site_obj.install_app.assert_called()
