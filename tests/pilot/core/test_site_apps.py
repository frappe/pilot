from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config import AppConfig, BenchConfig, MariaDBConfig, RedisConfig, SiteConfig, WorkerConfig
from pilot.core.app import App
from pilot.core.bench import Bench
from pilot.core.site import Site
from pilot.core.site.apps import SiteApps
from pilot.core.site.config.database_state import SiteDatabaseState
from pilot.exceptions import BenchError, CommandError
from tests.pilot.marketplace_registry import publish


def make_bench(tmp_path: Path) -> Bench:
    config = BenchConfig(
        name="test-bench",
        python_version="3.14",
        apps=[AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="version-16")],
        mariadb=MariaDBConfig(root_password="root"),
        redis=RedisConfig(cache_port=13000, queue_port=11000),
        workers=WorkerConfig(),
    )
    return Bench(config, tmp_path)


def make_site_and_app(tmp_path: Path) -> tuple[Site, App]:
    bench = make_bench(tmp_path)
    bench.create_directories()
    site = Site(SiteConfig(name="site1.localhost", apps=["frappe"]), bench)
    app = App(AppConfig(name="erpnext", repo="https://github.com/frappe/erpnext", branch="version-16"), bench)
    return site, app


def test_install_app_clears_cache_before_and_after_success(tmp_path: Path) -> None:
    site, app = make_site_and_app(tmp_path)

    with (
        patch("pilot.core.site.apps.run_command") as mock_rc,
        patch.object(Bench, "reload_workers"),
    ):
        site.install_app(app)

    commands = [call.args[0] for call in mock_rc.call_args_list]
    assert len(commands) == 3
    assert commands[0][-1] == "clear-cache"
    assert commands[1][-2:] == ["install-app", "erpnext"]
    assert commands[2][-1] == "clear-cache"


def test_install_app_passes_force_to_frappe(tmp_path: Path) -> None:
    site, app = make_site_and_app(tmp_path)

    with (
        patch("pilot.core.site.apps.run_command") as mock_rc,
        patch.object(Bench, "reload_workers"),
    ):
        site.install_app(app, force=True)

    commands = [call.args[0] for call in mock_rc.call_args_list]
    assert commands[1][-3:] == ["install-app", "erpnext", "--force"]


def test_install_app_clears_cache_after_failure(tmp_path: Path) -> None:
    site, app = make_site_and_app(tmp_path)

    def fail_on_install(argv, **kwargs):
        if "install-app" in argv:
            raise CommandError("boom")

    with (
        patch("pilot.core.site.apps.run_command", side_effect=fail_on_install) as mock_rc,
        patch.object(Bench, "reload_workers"),
        pytest.raises(CommandError),
    ):
        site.install_app(app)

    commands = [call.args[0] for call in mock_rc.call_args_list]
    assert len(commands) == 3
    assert commands[0][-1] == "clear-cache"
    assert commands[1][-2:] == ["install-app", "erpnext"]
    assert commands[2][-1] == "clear-cache"


def _fail_on_install(argv, **kwargs):
    if "install-app" in argv:
        raise CommandError("after_install failed")


def _write_site_config(site: Site, config: dict) -> None:
    site.path.mkdir(parents=True, exist_ok=True)
    (site.path / "site_config.json").write_text(json.dumps(config))


def test_failed_install_after_frappe_lists_the_app_is_marked_unfinished(tmp_path: Path) -> None:
    site, app = make_site_and_app(tmp_path)
    _write_site_config(site, {"db_name": "x"})

    with (
        patch("pilot.core.site.apps.run_command", side_effect=_fail_on_install),
        patch.object(SiteApps, "get_committed_apps", side_effect=[["frappe"], ["frappe", "erpnext"]]),
        patch.object(SiteDatabaseState, "has_module_rows", return_value=False),
        pytest.raises(CommandError, match="after_install failed"),
    ):
        site.install_app(app)

    assert site.unfinished_installs == ["erpnext"]


def test_failed_install_before_frappe_lists_the_app_is_not_marked(tmp_path: Path) -> None:
    site, app = make_site_and_app(tmp_path)
    _write_site_config(site, {})

    with (
        patch("pilot.core.site.apps.run_command", side_effect=_fail_on_install),
        patch.object(SiteApps, "get_committed_apps", return_value=["frappe"]),
        patch.object(SiteDatabaseState, "has_module_rows", return_value=False),
        pytest.raises(CommandError),
    ):
        site.install_app(app)

    assert site.unfinished_installs == []


def test_retrying_an_unfinished_install_forces_it_and_clears_the_mark(tmp_path: Path) -> None:
    site, app = make_site_and_app(tmp_path)
    _write_site_config(site, {"pilot_unfinished_installs": ["erpnext"]})

    with (
        patch("pilot.core.site.apps.run_command") as mock_rc,
        patch.object(SiteApps, "get_committed_apps", return_value=["frappe", "erpnext"]),
        patch.object(Bench, "reload_workers"),
    ):
        site.install_app(app)

    commands = [call.args[0] for call in mock_rc.call_args_list]
    assert commands[1][-3:] == ["install-app", "erpnext", "--force"]
    assert site.unfinished_installs == []


def test_retry_after_a_failed_sync_forces_past_leftover_module_rows(tmp_path: Path) -> None:
    site, app = make_site_and_app(tmp_path)

    with (
        patch("pilot.core.site.apps.run_command") as mock_rc,
        patch.object(SiteApps, "get_committed_apps", return_value=["frappe"]),
        patch.object(SiteDatabaseState, "has_module_rows", return_value=True),
    ):
        site.install_app(app)

    commands = [call.args[0] for call in mock_rc.call_args_list]
    assert commands[1][-3:] == ["install-app", "erpnext", "--force"]


def test_uninstall_app_clears_cache_after_success(tmp_path: Path) -> None:
    site, app = make_site_and_app(tmp_path)

    with (
        patch("pilot.core.site.apps.run_command") as mock_rc,
        patch.object(Bench, "reload_workers"),
    ):
        site.uninstall_app(app)

    commands = [call.args[0] for call in mock_rc.call_args_list]
    assert len(commands) == 2
    assert "uninstall-app" in commands[0]
    assert commands[1][-1] == "clear-cache"


@pytest.mark.parametrize(
    "committed, expected",
    [(["frappe"], ["frappe"]), (None, ["frappe", "erpnext"])],
)
def test_uninstall_puts_back_the_committed_app_list(tmp_path: Path, committed, expected) -> None:
    """Frappe can copy a stale cached list to site_config.json after an uninstall; the
    committed setting wins. An unreadable database leaves the file as it is."""
    import json

    site, app = make_site_and_app(tmp_path)
    site.path.mkdir(parents=True)
    config_path = site.path / "site_config.json"
    config_path.write_text(json.dumps({"db_name": "site1", "installed_apps": ["frappe", "erpnext"]}))

    with (
        patch("pilot.core.site.apps.run_command"),
        patch.object(SiteDatabaseState, "committed_apps", committed),
        patch.object(Bench, "reload_workers"),
    ):
        site.uninstall_app(app)

    config = json.loads(config_path.read_text())
    assert config["installed_apps"] == expected
    assert config["db_name"] == "site1"


def test_uninstall_app_clears_cache_after_failure(tmp_path: Path) -> None:
    site, app = make_site_and_app(tmp_path)

    def fail_on_uninstall(argv, **kwargs):
        raise CommandError("boom")

    with (
        patch("pilot.core.site.apps.run_command", side_effect=fail_on_uninstall) as mock_rc,
        patch.object(Bench, "reload_workers"),
        pytest.raises(CommandError),
    ):
        site.uninstall_app(app)

    commands = [call.args[0] for call in mock_rc.call_args_list]
    assert len(commands) == 2
    assert "uninstall-app" in commands[0]
    assert commands[1][-1] == "clear-cache"


def test_under_maintenance_restores_the_previous_settings(tmp_path: Path) -> None:
    """A site that was already down stays down - the guard restores what it read,
    it does not blindly switch maintenance off."""
    from unittest.mock import MagicMock

    from pilot.core.site import Site

    site = MagicMock(spec=Site)
    site.maintenance_settings = {"maintenance_mode": 1, "pause_scheduler": 0}

    with Site.under_maintenance(site):
        site.set_maintenance_mode.assert_called_once_with(True)

    site.set_maintenance_settings.assert_called_once_with(
        {"maintenance_mode": 1, "pause_scheduler": 0}
    )


def test_under_maintenance_lifts_maintenance_when_the_body_raises(tmp_path: Path) -> None:
    """A failed install must not strand the site in maintenance mode."""
    from unittest.mock import MagicMock

    import pytest

    from pilot.core.site import Site

    site = MagicMock(spec=Site)
    site.maintenance_settings = {"maintenance_mode": 0, "pause_scheduler": 0}

    with pytest.raises(RuntimeError), Site.under_maintenance(site):
        raise RuntimeError("install blew up")

    site.set_maintenance_settings.assert_called_once_with(
        {"maintenance_mode": 0, "pause_scheduler": 0}
    )


def test_install_app_enables_it_when_the_site_has_it_disabled(tmp_path: Path) -> None:
    """A disabled app keeps its schema and data, so reinstalling is a flag flip."""
    site, app = make_site_and_app(tmp_path)

    with (
        patch("pilot.core.site.apps.run_command") as mock_rc,
        patch.object(SiteApps, "disabled_apps", return_value=["erpnext"]),
        patch.object(Bench, "reload_workers"),
    ):
        site.install_app(app)

    commands = [call.args[0] for call in mock_rc.call_args_list]
    assert len(commands) == 1
    assert commands[0][-2:] == ["enable-app", "erpnext"]


def test_disable_app_calls_frappe_for_a_marketplace_app(tmp_path: Path) -> None:
    site, app = make_site_and_app(tmp_path)
    publish([{"name": "erpnext", "repo": "https://github.com/frappe/erpnext"}])

    with (
        patch("pilot.core.site.apps.run_command") as mock_rc,
        patch.object(Bench, "has_app_disabling", True),
    ):
        site.disable_app(app)

    assert mock_rc.call_args_list[0].args[0][-2:] == ["disable-app", "erpnext"]


def test_disable_app_rejects_an_app_outside_the_marketplace(tmp_path: Path) -> None:
    """Re-enabling means installing the app again, which needs it in the catalog."""
    site, app = make_site_and_app(tmp_path)
    publish([{"name": "hrms", "repo": "https://github.com/frappe/hrms"}])

    with (
        patch("pilot.core.site.apps.run_command") as mock_rc,
        patch.object(Bench, "has_app_disabling", True),
        pytest.raises(BenchError),
    ):
        site.disable_app(app)

    mock_rc.assert_not_called()


def test_disable_app_rejects_a_frappe_without_the_disabled_docfield(tmp_path: Path) -> None:
    site, app = make_site_and_app(tmp_path)
    publish([{"name": "erpnext", "repo": "https://github.com/frappe/erpnext"}])
    with (
        patch("pilot.core.site.apps.run_command") as mock_rc,
        patch.object(Bench, "has_app_disabling", False),
        pytest.raises(BenchError),
    ):
        site.disable_app(app)

    mock_rc.assert_not_called()


def test_enable_app_brings_a_disabled_dependency_back_first(tmp_path: Path) -> None:
    """Frappe refuses to enable an app whose required app is still off, so the
    dependency is flipped before the app that needs it."""
    site, app = make_site_and_app(tmp_path)
    (tmp_path / "apps" / "telephony").mkdir(parents=True)

    with (
        patch("pilot.core.site.apps.run_command") as mock_rc,
        patch.object(SiteApps, "get_required_apps", side_effect=lambda a: ["telephony"] if a is app else []),
        patch.object(SiteApps, "disabled_apps", return_value=["erpnext", "telephony"]),
        patch.object(SiteApps, "installed_apps", return_value=["frappe", "erpnext", "telephony"]),
    ):
        site.enable_app(app)

    commands = [call.args[0][-2:] for call in mock_rc.call_args_list]
    assert commands == [["enable-app", "telephony"], ["enable-app", "erpnext"]]


def test_missing_dependencies_lists_what_the_site_does_not_have(tmp_path: Path) -> None:
    site, app = make_site_and_app(tmp_path)

    with (
        patch.object(SiteApps, "get_required_apps", return_value=["telephony", "frappe"]),
        patch.object(SiteApps, "installed_apps", return_value=["frappe", "erpnext"]),
    ):
        assert site.get_missing_dependencies(app) == ["telephony"]


def test_install_app_refuses_when_a_required_app_is_not_on_the_bench(tmp_path: Path) -> None:
    site, app = make_site_and_app(tmp_path)

    with (
        patch("pilot.core.site.apps.run_command") as mock_rc,
        patch.object(SiteApps, "disabled_apps", return_value=[]),
        patch.object(SiteApps, "get_required_apps", return_value=["telephony"]),
        pytest.raises(BenchError, match="requires telephony"),
    ):
        site.install_app(app)

    assert not any("install-app" in call.args[0] for call in mock_rc.call_args_list)


def test_uninstalling_an_app_the_bench_lacks_only_drops_it_from_the_site(tmp_path) -> None:
    from unittest.mock import patch

    from pilot.core.site.apps import SiteApps

    bench = make_bench(tmp_path)
    bench.create_directories()
    site_dir = bench.sites_path / "site1.localhost"
    site_dir.mkdir()
    (site_dir / "site_config.json").write_text("{}")
    site = bench.site("site1.localhost")

    with (
        patch("pilot.core.site.Site.list_apps", return_value=["frappe", "ghost"]),
        patch("pilot.core.site.apps.run_command") as run,
        patch.object(SiteApps, "remove_app_if_not_on_any_site") as remove_from_bench,
    ):
        site.uninstall_apps(["ghost"])

    commands = [call.args[0] for call in run.call_args_list]
    assert any(command[-2:] == ["remove-from-installed-apps", "ghost"] for command in commands)
    assert not any("uninstall-app" in command for command in commands)
    remove_from_bench.assert_not_called()


@pytest.mark.parametrize("remove_from_bench", [False, True])
def test_uninstalling_from_a_site_removes_the_app_from_the_bench_only_when_asked(
    tmp_path, remove_from_bench: bool
) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    (bench.apps_path / "myapp").mkdir()
    site_dir = bench.sites_path / "site1.localhost"
    site_dir.mkdir()
    (site_dir / "site_config.json").write_text("{}")

    with (
        patch("pilot.core.site.Site.list_apps", return_value=["frappe", "myapp"]),
        patch("pilot.core.site.Site.uninstall_app"),
        patch.object(SiteApps, "remove_app_if_not_on_any_site") as remove_app,
    ):
        bench.site("site1.localhost").uninstall_apps(["myapp"], remove_from_bench=remove_from_bench)

    assert remove_app.called is remove_from_bench
