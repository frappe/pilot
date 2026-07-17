"""Tests for GetAppCommand.run() (delegating to App.install()) — an
already-installed app skips clone, validate, install, and build, but its
dependencies are still installed (missing ones) or resolved (already-present
ones) so callers can still install them onto new sites."""
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
        target_type="branch",
        target="main",
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

    with patch.object(App, "clone") as mock_clone, \
            patch.object(App, "_validate") as mock_validate, \
            patch.object(App, "_install_into_environment") as mock_install, \
            patch.object(App, "_build_assets_via_env_manager") as mock_build:
        cmd.run()

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

    with patch.object(App, "clone") as mock_clone, \
            patch.object(App, "_validate") as mock_validate, \
            patch.object(App, "_install_into_environment") as mock_install, \
            patch.object(App, "_build_assets_via_env_manager") as mock_build:
        cmd.run()

    mock_clone.assert_not_called()
    mock_validate.assert_not_called()
    mock_install.assert_not_called()
    mock_build.assert_not_called()


def test_short_circuit_adopts_real_on_disk_app_path(tmp_path: Path) -> None:
    """Regression: a hyphenated repo name's raw path never existed on disk —
    only the module-normalized folder from an earlier run does. cmd.app must
    point at the real folder so callers (e.g. get_and_install_app_task) don't
    get an App referencing a nonexistent path."""
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
    """Defect: when run() short-circuits on an already-registered app,
    installed_dependencies must still list its marketplace dependencies —
    otherwise GetAndInstallAppTask only installs the primary app onto new
    sites and silently leaves its dependencies uninstalled there."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.apps_path / "helpdesk").mkdir(parents=True)
    (bench.apps_path / "telephony").mkdir(parents=True)
    (bench.sites_path / "apps.txt").write_text("frappe\ntelephony\nhelpdesk\n")

    telephony = make_resolver("telephony")
    helpdesk = make_resolver("helpdesk", deps={"telephony": ""})
    helpdesk._registry = {"telephony": [telephony]}

    with patch.object(Marketplace, "read_all_apps", return_value=[helpdesk]), \
            patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"), \
            patch.object(Marketplace, "_read_apps_json", return_value="[]"):
        cmd = GetAppCommand(bench, repo="https://github.com/frappe/helpdesk", install_dependencies=True)
        cmd.run()

    assert [app.config.name for app in cmd.installed_dependencies] == ["telephony"]


def test_still_installs_missing_dependency_when_parent_already_installed(tmp_path: Path) -> None:
    """A dependency can still be missing even when the parent app itself is
    already installed (e.g. helpdesk was installed before telephony existed,
    or the app is being added to a new site) — it must still get installed,
    otherwise that site breaks."""
    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.apps_path / "helpdesk").mkdir(parents=True)
    (bench.sites_path / "apps.txt").write_text("frappe\nhelpdesk\n")  # telephony missing

    telephony = make_resolver("telephony")
    helpdesk = make_resolver("helpdesk", deps={"telephony": ""})
    helpdesk._registry = {"telephony": [telephony]}

    with patch.object(Marketplace, "read_all_apps", return_value=[helpdesk]), \
            patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"), \
            patch.object(Marketplace, "_read_apps_json", return_value="[]"), \
            patch.object(App, "clone", autospec=True, side_effect=lambda app: app.path.mkdir(parents=True, exist_ok=True)) as mock_clone, \
            patch.object(App, "_validate"), \
            patch.object(App, "_install_into_environment"), \
            patch.object(App, "_build_assets_via_env_manager"):
        cmd = GetAppCommand(bench, repo="https://github.com/frappe/helpdesk", install_dependencies=True)
        cmd.run()

    # helpdesk itself short-circuits (already installed); telephony is the
    # only app that needed an actual clone.
    mock_clone.assert_called_once()
    assert [app.config.name for app in cmd.installed_dependencies] == ["telephony"]


def test_skip_validations_flag_still_skips_validate(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    cmd = GetAppCommand(bench, repo="https://github.com/frappe/myapp", skip_validations=True)

    with patch.object(App, "clone"), \
            patch.object(App, "_validate") as mock_validate, \
            patch.object(App, "_install_into_environment"), \
            patch.object(App, "_build_assets_via_env_manager"):
        cmd.run()

    mock_validate.assert_not_called()


def test_bench_is_app_installed_reflects_apps_txt_contents(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    assert bench.is_app_installed("erpnext") is False

    (bench.sites_path / "apps.txt").write_text("frappe\nerpnext\n")
    assert bench.is_app_installed("erpnext") is True
