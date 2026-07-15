"""Tests for admin.backend.tasks.jobs.new_site_task.

The new-site wizard lets users pick marketplace apps that haven't been
`get-app`'d onto this bench yet, which used to fail deep inside
NewSiteCommand._validate() with a "run bench get-app" error. NewSiteTask
should fetch any such apps first.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from admin.backend.tasks.jobs.new_site_task import NewSiteTask
from pilot.core.marketplace import Marketplace, Resolver
from tests.test_commands import make_bench


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

    with patch("pilot.commands.get_app.GetAppCommand.run") as mock_run:
        task._fetch_missing_apps()

    mock_run.assert_not_called()


def test_fetch_missing_apps_fetches_app_not_on_bench(tmp_path: Path) -> None:
    task = make_task(tmp_path, ["frappe", "helpdesk"])
    (task.bench.sites_path / "apps.txt").write_text("frappe\n")

    frappe_helpdesk = resolver("helpdesk")
    frappe_helpdesk._registry = {"helpdesk": [frappe_helpdesk]}

    with patch.object(Marketplace, "read_all_apps", return_value=[frappe_helpdesk]), \
            patch("pilot.commands.get_app.GetAppCommand.run") as mock_run, \
            patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"):
        task._fetch_missing_apps()

    mock_run.assert_called_once()


def test_fetch_missing_apps_resolves_dependencies_first(tmp_path: Path) -> None:
    task = make_task(tmp_path, ["payments"])

    dep = resolver("frappe_payments_dep")
    top = resolver("payments", deps={"frappe_payments_dep": ""})
    top._registry = {"frappe_payments_dep": [dep]}

    fetched: list[str] = []

    def fake_run(self):
        fetched.append(self.name)

    with patch.object(Marketplace, "read_all_apps", return_value=[top]), \
            patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"), \
            patch("pilot.commands.get_app.GetAppCommand.run", fake_run):
        task._fetch_missing_apps()

    assert fetched == ["frappe_payments_dep", "payments"]


def test_fetch_missing_apps_raises_when_not_in_marketplace(tmp_path: Path) -> None:
    from pilot.exceptions import BenchError

    task = make_task(tmp_path, ["unknown_app"])

    with patch.object(Marketplace, "read_all_apps", return_value=[]), \
            patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"):
        try:
            task._fetch_missing_apps()
        except BenchError as error:
            assert "not found in marketplace" in str(error)
        else:
            raise AssertionError("expected BenchError")
