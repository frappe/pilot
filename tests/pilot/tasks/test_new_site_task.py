"""Tests for NewSiteTask."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pilot.core.app import App
from pilot.integrations.marketplace import Marketplace, Resolver
from pilot.tasks.new_site import NewSiteTask
from tests.pilot.commands.test_commands import make_bench


def resolver(name: str, deps: dict[str, str] | None = None) -> Resolver:
    return Resolver(
        app=name,
        repo=f"https://github.com/frappe/{name}",
        branch="main",
        commit="a" * 40,
        channel="stable",
        version="1.0.0",
        frappe_version="16.0.0",
        required_version="",
        is_installable=True,
        dependencies=deps or {},
    )


def make_task(bench_root: Path, apps: list[str]) -> NewSiteTask:
    bench = make_bench(bench_root)
    bench.create_directories()
    return NewSiteTask(
        bench=bench,
        bench_root=bench_root,
        name="site1.localhost",
        admin_password="site-secret",
        db_type=None,
        apps=apps,
    )


def test_fetch_missing_apps_skips_apps_already_in_apps_txt(tmp_path: Path) -> None:
    task = make_task(tmp_path, ["frappe"])
    (task.bench.sites_path / "apps.txt").write_text("frappe\n")

    with patch.object(App, "install") as mock_install:
        task.fetch_missing_apps()

    mock_install.assert_not_called()


def test_fetch_missing_apps_fetches_app_not_on_bench(tmp_path: Path) -> None:
    task = make_task(tmp_path, ["frappe", "helpdesk"])
    (task.bench.sites_path / "apps.txt").write_text("frappe\n")

    frappe_helpdesk = resolver("helpdesk")
    frappe_helpdesk._registry = {"helpdesk": [frappe_helpdesk]}

    with (
        patch.object(Marketplace, "read_all_apps", return_value=[frappe_helpdesk]),
        patch.object(App, "install") as mock_install,
        patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"),
        patch.object(Marketplace, "_load_registry", return_value=[]),
    ):
        task.fetch_missing_apps()

    mock_install.assert_called_once()


def test_fetch_missing_apps_installs_dependencies_via_get_app(tmp_path: Path) -> None:
    """Dependency resolution now happens inside App.install() itself
    (install_dependencies=True), not as a separate fetch loop here."""
    task = make_task(tmp_path, ["payments"])

    dep = resolver("frappe_payments_dep")
    top = resolver("payments", deps={"frappe_payments_dep": ""})
    top._registry = {"frappe_payments_dep": [dep]}

    with (
        patch.object(Marketplace, "read_all_apps", return_value=[top]),
        patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"),
        patch.object(Marketplace, "_load_registry", return_value=[]),
        patch.object(App, "install") as mock_install,
    ):
        task.fetch_missing_apps()

    mock_install.assert_called_once()
    _, kwargs = mock_install.call_args
    assert kwargs["install_dependencies"] is True


def test_fetch_missing_apps_raises_when_not_in_marketplace(tmp_path: Path) -> None:
    from pilot.exceptions import BenchError

    task = make_task(tmp_path, ["unknown_app"])

    with (
        patch.object(Marketplace, "read_all_apps", return_value=[]),
        patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"),
        patch.object(Marketplace, "_load_registry", return_value=[]),
    ):
        try:
            task.fetch_missing_apps()
        except BenchError as error:
            assert "not found in marketplace" in str(error)
        else:
            raise AssertionError("expected BenchError")


def test_run_reloads_workers_after_fetching_apps_and_before_provisioning(tmp_path: Path) -> None:
    """Provisioning runs install-app, which enqueues jobs importing the site's
    apps - workers must restart in between or those jobs fail to import."""
    from unittest.mock import MagicMock, call

    task = make_task(tmp_path, ["helpdesk"])
    order = MagicMock()
    task.bench.reload_workers = order.reload

    with (
        patch.object(task, "require_production_privileges"),
        patch.object(task, "fetch_missing_apps", order.fetch),
        patch.object(task, "create", order.create),
    ):
        task.run()

    assert order.mock_calls == [call.fetch(), call.reload(), call.create()]


def test_run_reloads_workers_even_when_no_app_was_fetched(tmp_path: Path) -> None:
    """An app already on the bench can still postdate the running workers - it
    may have arrived through `pilot get-app` after they started."""
    from unittest.mock import MagicMock

    task = make_task(tmp_path, ["frappe"])
    (task.bench.sites_path / "apps.txt").write_text("frappe\n")
    task.bench.reload_workers = MagicMock()

    with (
        patch.object(task, "require_production_privileges"),
        patch.object(task, "create"),
    ):
        task.run()

    task.bench.reload_workers.assert_called_once()


def test_failure_cleanup_targets_a_site_this_task_would_create(tmp_path: Path) -> None:
    task = make_task(tmp_path, ["frappe"])

    assert task.remove_failed_site() == {"site": "site1.localhost"}


def test_failure_cleanup_never_targets_a_site_that_already_exists(tmp_path: Path) -> None:
    task = make_task(tmp_path, ["frappe"])
    site_path = task.bench.sites_path / "site1.localhost"
    site_path.mkdir(parents=True)
    (site_path / "site_config.json").write_text("{}")

    assert task.remove_failed_site() is None
