"""Tests for GetAppCommand.run()."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pilot.commands.apps.download import GetAppCommand
from pilot.core.app import App
from pilot.integrations.marketplace import Marketplace, Resolver
from tests.pilot.commands.test_commands import make_bench


def make_resolver(name: str, deps: dict[str, str] | None = None) -> Resolver:
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


def test_full_flow_runs_when_app_not_registered(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    cmd = GetAppCommand(bench, repo="https://github.com/frappe/myapp")

    with (
        patch.object(
            App,
            "clone",
            autospec=True,
            side_effect=lambda app, on_progress=None: app.path.mkdir(parents=True, exist_ok=True),
        ) as mock_clone,
        patch.object(App, "validate") as mock_validate,
        patch.object(App, "_install_into_environment") as mock_install,
        patch.object(App, "_build_assets_via_env_manager") as mock_build,
        patch.object(type(bench), "reload_workers") as mock_reload,
    ):
        cmd.run()

    # Production workers must restart to import the new app.
    mock_reload.assert_called_once()
    mock_clone.assert_called_once()
    mock_validate.assert_called_once()
    mock_install.assert_called_once()
    mock_build.assert_called_once()


def test_run_short_circuits_when_app_already_registered(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.apps_path / "myapp").mkdir(parents=True)  # registered apps always have a real folder
    (bench.sites_path / "apps.txt").write_text("frappe\nmyapp\n")
    cmd = GetAppCommand(bench, repo="https://github.com/frappe/myapp")

    with (
        patch.object(App, "clone") as mock_clone,
        patch.object(App, "validate") as mock_validate,
        patch.object(App, "_install_into_environment") as mock_install,
        patch.object(App, "_build_assets_via_env_manager") as mock_build,
        patch.object(type(bench), "reload_workers") as mock_reload,
    ):
        cmd.run()

    mock_reload.assert_not_called()
    mock_clone.assert_not_called()
    mock_validate.assert_not_called()
    mock_install.assert_not_called()
    mock_build.assert_not_called()


def test_short_circuit_adopts_real_on_disk_app_path(tmp_path: Path) -> None:
    """Regression: short-circuit uses the normalized on-disk app path."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    real_app_dir = bench.apps_path / "india_compliance"
    real_app_dir.mkdir(parents=True)
    (bench.sites_path / "apps.txt").write_text("frappe\nindia_compliance\n")

    cmd = GetAppCommand(bench, repo="https://github.com/frappe/india-compliance")
    cmd.run()

    assert cmd.app.path == real_app_dir
    assert cmd.app.path.is_dir()
    assert cmd.app.config.name == "india_compliance"


def test_short_circuit_still_populates_installed_dependencies(tmp_path: Path) -> None:
    """Regression: short-circuit still reports installed dependencies."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.apps_path / "helpdesk").mkdir(parents=True)
    (bench.apps_path / "telephony").mkdir(parents=True)
    (bench.sites_path / "apps.txt").write_text("frappe\ntelephony\nhelpdesk\n")

    telephony = make_resolver("telephony")
    helpdesk = make_resolver("helpdesk", deps={"telephony": ""})
    helpdesk._registry = {"telephony": [telephony]}

    with (
        patch.object(Marketplace, "read_all_apps", return_value=[helpdesk]),
        patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"),
        patch.object(Marketplace, "_load_registry", return_value=[]),
    ):
        cmd = GetAppCommand(bench, repo="https://github.com/frappe/helpdesk", install_dependencies=True)
        cmd.run()

    assert [app.config.name for app in cmd.installed_dependencies] == ["telephony"]


def test_still_installs_missing_dependency_when_parent_already_installed(tmp_path: Path) -> None:
    """Missing dependencies are installed even when the parent app exists."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.apps_path / "helpdesk").mkdir(parents=True)
    (bench.sites_path / "apps.txt").write_text("frappe\nhelpdesk\n")  # telephony missing

    telephony = make_resolver("telephony")
    helpdesk = make_resolver("helpdesk", deps={"telephony": ""})
    helpdesk._registry = {"telephony": [telephony]}

    with (
        patch.object(Marketplace, "read_all_apps", return_value=[helpdesk]),
        patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"),
        patch.object(Marketplace, "_load_registry", return_value=[]),
        patch.object(
            App,
            "clone",
            autospec=True,
            side_effect=lambda app, on_progress=None: app.path.mkdir(parents=True, exist_ok=True),
        ) as mock_clone,
        patch.object(App, "checkout_commit"),
        patch.object(App, "validate"),
        patch.object(App, "_install_into_environment"),
        patch.object(App, "_build_assets_via_env_manager"),
    ):
        cmd = GetAppCommand(bench, repo="https://github.com/frappe/helpdesk", install_dependencies=True)
        cmd.run()

    # helpdesk itself short-circuits (already installed); telephony is the
    # only app that needed an actual clone.
    mock_clone.assert_called_once()
    assert [app.config.name for app in cmd.installed_dependencies] == ["telephony"]


def test_get_app_always_validates(tmp_path: Path) -> None:
    """There is no way past the checks. An app that cannot pass them cannot be
    installed, so nothing reaches a bench unvalidated."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    cmd = GetAppCommand(bench, repo="https://github.com/frappe/myapp")

    with (
        patch.object(
            App,
            "clone",
            autospec=True,
            side_effect=lambda app, on_progress=None: app.path.mkdir(parents=True, exist_ok=True),
        ),
        patch.object(App, "validate") as mock_validate,
        patch.object(App, "_install_into_environment"),
        patch.object(App, "_build_assets_via_env_manager"),
    ):
        cmd.run()

    mock_validate.assert_called_once()


def test_bench_is_app_installed_reflects_apps_txt_contents(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    assert bench.is_app_installed("erpnext") is False

    (bench.sites_path / "apps.txt").write_text("frappe\nerpnext\n")
    assert bench.is_app_installed("erpnext") is True


def test_without_a_branch_the_repository_default_is_used(tmp_path: Path) -> None:
    # Many Frappe apps default to develop and have no main branch.
    bench = make_bench(tmp_path)

    cmd = GetAppCommand(bench, repo="https://github.com/frappe/telephony")

    assert cmd.app.config.branch == ""
