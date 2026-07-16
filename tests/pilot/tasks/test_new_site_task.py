"""Tests for pilot.tasks.jobs.new_site_task.

The new-site wizard lets users pick marketplace apps that haven't been
`get-app`'d onto this bench yet, which used to fail deep inside
NewSiteCommand._validate() with a "run bench get-app" error. NewSiteTask
should fetch any such apps first.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from pilot.tasks.jobs.new_site_task import NewSiteTask
from pilot.integrations.marketplace import Marketplace, Resolver
from tests.pilot.commands.test_commands import make_bench


def resolver(name: str, deps: dict[str, str] | None = None) -> Resolver:
    return Resolver(
        app=name,
        repo=f"https://github.com/frappe/{name}",
        target_type="branch",
        target="main",
        version="1.0.0",
        frappe_version="16.0.0",
        required_version="",
        is_installable=True,
        dependencies=deps or {},
    )


def make_task(bench_root: Path, apps: list[str]) -> NewSiteTask:
    args = MagicMock(name="site1.localhost", admin_password="site-secret", db_type=None, apps=apps)
    args.name = "site1.localhost"
    bench = make_bench(bench_root)
    bench.create_directories()
    return NewSiteTask(bench, bench_root, args)


def test_fetch_missing_apps_skips_apps_already_in_apps_txt(tmp_path: Path) -> None:
    task = make_task(tmp_path, ["frappe"])
    (task.bench.sites_path / "apps.txt").write_text("frappe\n")

    with patch("pilot.commands.apps.download.GetAppCommand.run") as mock_run:
        task._fetch_missing_apps()

    mock_run.assert_not_called()


def test_fetch_missing_apps_fetches_app_not_on_bench(tmp_path: Path) -> None:
    task = make_task(tmp_path, ["frappe", "helpdesk"])
    (task.bench.sites_path / "apps.txt").write_text("frappe\n")

    frappe_helpdesk = resolver("helpdesk")
    frappe_helpdesk._registry = {"helpdesk": [frappe_helpdesk]}

    with patch.object(Marketplace, "read_all_apps", return_value=[frappe_helpdesk]), \
            patch("pilot.commands.apps.download.GetAppCommand.run") as mock_run, \
            patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"), \
            patch.object(Marketplace, "_read_apps_json", return_value="[]"):
        task._fetch_missing_apps()

    mock_run.assert_called_once()


def test_fetch_missing_apps_installs_dependencies_via_get_app(tmp_path: Path) -> None:
    """Dependency resolution now happens inside GetAppCommand itself
    (install_dependencies=True), not as a separate fetch loop here."""
    task = make_task(tmp_path, ["payments"])

    dep = resolver("frappe_payments_dep")
    top = resolver("payments", deps={"frappe_payments_dep": ""})
    top._registry = {"frappe_payments_dep": [dep]}

    with patch.object(Marketplace, "read_all_apps", return_value=[top]), \
            patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"), \
            patch.object(Marketplace, "_read_apps_json", return_value="[]"), \
            patch("pilot.tasks.jobs.new_site_task.GetAppCommand") as mock_cmd:
        task._fetch_missing_apps()

    mock_cmd.assert_called_once_with(task.bench, top.repo, top.target, install_dependencies=True)
    mock_cmd.return_value.run.assert_called_once()


def test_fetch_missing_apps_raises_when_not_in_marketplace(tmp_path: Path) -> None:
    from pilot.exceptions import BenchError

    task = make_task(tmp_path, ["unknown_app"])

    with patch.object(Marketplace, "read_all_apps", return_value=[]), \
            patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"), \
            patch.object(Marketplace, "_read_apps_json", return_value="[]"):
        try:
            task._fetch_missing_apps()
        except BenchError as error:
            assert "not found in marketplace" in str(error)
        else:
            raise AssertionError("expected BenchError")
