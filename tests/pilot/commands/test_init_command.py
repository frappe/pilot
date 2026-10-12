"""`pilot init`: existing database handling and the --no-dev opt-out."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.commands.bench.initialize import InitCommand
from pilot.config import AppConfig, BenchConfig
from pilot.core.bench import Bench
from pilot.core.bench.initializer import BenchInitializer
from pilot.exceptions import BenchError
from tests.pilot.commands.test_commands import make_bench


def _initializer() -> BenchInitializer:
    return BenchInitializer(MagicMock())


def test_provisions_a_pilot_owned_server() -> None:
    manager = MagicMock()
    manager.config.existing = False

    _initializer()._provision_or_verify(manager, "MariaDB")

    manager.provision.assert_called_once()
    manager.has_valid_credentials.assert_not_called()


def test_verifies_credentials_for_an_existing_server_without_provisioning() -> None:
    manager = MagicMock()
    manager.config.existing = True
    manager.has_valid_credentials.return_value = True

    _initializer()._provision_or_verify(manager, "MariaDB")

    manager.provision.assert_not_called()
    manager.has_valid_credentials.assert_called_once()


def test_raises_when_existing_credentials_are_wrong() -> None:
    manager = MagicMock()
    manager.config.existing = True
    manager.config.host = "db.example.com"
    manager.config.port = 3306
    manager.config.admin_user = "admin"
    manager.has_valid_credentials.return_value = False

    with pytest.raises(BenchError, match=r"db\.example\.com"):
        _initializer()._provision_or_verify(manager, "MariaDB")

    manager.provision.assert_not_called()


def test_ensure_database_credentials_generates_for_a_fresh_server() -> None:
    bench = MagicMock()
    bench.config.db_type = "mariadb"
    bench.config.mariadb.existing = False
    bench.config.mariadb.root_password = ""
    bench.config.mariadb.port = 3306

    with patch("pilot.utils.pick_free_port", return_value=3306):
        BenchInitializer(bench)._ensure_database_credentials()

    assert bench.config.mariadb.root_password
    bench.config.write.assert_called_once_with(bench.path)


def test_ensure_database_credentials_skips_when_already_set() -> None:
    bench = MagicMock()
    bench.config.db_type = "mariadb"
    bench.config.mariadb.existing = False
    bench.config.mariadb.root_password = "already-set"

    BenchInitializer(bench)._ensure_database_credentials()

    bench.config.write.assert_not_called()


def test_ensure_database_credentials_skips_for_an_existing_server() -> None:
    bench = MagicMock()
    bench.config.db_type = "mariadb"
    bench.config.mariadb.existing = True
    bench.config.mariadb.root_password = ""

    BenchInitializer(bench)._ensure_database_credentials()

    bench.config.write.assert_not_called()


def test_ensure_database_credentials_skips_sqlite() -> None:
    bench = MagicMock()
    bench.config.db_type = "sqlite"

    BenchInitializer(bench)._ensure_database_credentials()

    bench.config.write.assert_not_called()


def test_no_dev_persists_the_opt_out_before_initialising(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config.write(tmp_path)

    with patch.object(Bench, "initialize") as initialize:
        InitCommand(bench=bench, no_dev=True).run()

    initialize.assert_called_once()
    assert BenchConfig.read(tmp_path).install_dev_extra is False


def test_init_keeps_the_dev_extra_by_default(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config.write(tmp_path)

    with patch.object(Bench, "initialize"):
        InitCommand(bench=bench).run()

    assert BenchConfig.read(tmp_path).install_dev_extra is True


def test_new_bench_enables_server_scripts_unless_set_by_hand(tmp_path) -> None:
    import json

    bench = make_bench(tmp_path)
    bench.sites_path.mkdir(parents=True)
    config_path = bench.sites_path / "common_site_config.json"

    BenchInitializer(bench)._create_bench_structure()
    assert json.loads(config_path.read_text())["server_script_enabled"] is True

    config_path.write_text(json.dumps({"server_script_enabled": False}))
    BenchInitializer(bench)._create_bench_structure()
    assert json.loads(config_path.read_text())["server_script_enabled"] is False


def test_init_links_prebuilt_assets_after_node_dependencies(tmp_path: Path) -> None:
    from pilot.managers.environment import PythonEnvManager

    bench = make_bench(tmp_path)
    bench.create_directories()
    public = bench.apps_path / "frappe" / "frappe" / "public"
    public.mkdir(parents=True)
    (public / "desk.js").write_text("desk")
    app = MagicMock()
    app.path = public.parent.parent
    app.config.name = "frappe"
    app.has_source_changes = False
    app.has_page_islands = False
    initializer = BenchInitializer(bench)

    def install_node_dependencies(self: PythonEnvManager) -> None:
        (app.path / "node_modules").mkdir()
        (app.path / "node_modules" / "style.css").write_text("style")

    with (
        patch.object(Bench, "apps", return_value=[app]),
        patch.object(BenchInitializer, "_ensure_admin_password"),
        patch.object(BenchInitializer, "_ensure_database_credentials"),
        patch.object(BenchInitializer, "_install_system_packages"),
        patch.object(BenchInitializer, "_create_virtualenv"),
        patch.object(BenchInitializer, "_install_framework_apps"),
        patch.object(BenchInitializer, "_configure_redis"),
        patch.object(BenchInitializer, "_ensure_admin_frontend"),
        patch.object(BenchInitializer, "_generate_process_config"),
        patch.object(PythonEnvManager, "install_node"),
        patch.object(PythonEnvManager, "install_node_dependencies", install_node_dependencies),
        patch("pilot.core.app.prebuilt_assets.PrebuiltAssets.install", return_value=True),
    ):
        initializer.run(lambda message: None)

    assets = bench.sites_path / "assets" / "frappe"
    assert (assets / "desk.js").read_text() == "desk"
    assert (assets / "node_modules" / "style.css").read_text() == "style"


def test_init_rolls_back_when_an_app_asset_build_fails(tmp_path: Path) -> None:
    from pilot.managers.environment import PythonEnvManager

    bench = make_bench(tmp_path)
    bench.config.write(tmp_path)
    bench.apps_path.mkdir()
    (bench.apps_path / "local-work.txt").write_text("keep me")
    bench.sites_path.mkdir()
    (bench.sites_path / "existing.txt").write_text("keep me too")
    app = MagicMock()
    messages: list[str] = []

    with (
        patch.object(Bench, "apps", return_value=[app]),
        patch.object(BenchInitializer, "_ensure_admin_password"),
        patch.object(BenchInitializer, "_ensure_database_credentials"),
        patch.object(BenchInitializer, "_install_system_packages"),
        patch.object(BenchInitializer, "_create_virtualenv"),
        patch.object(BenchInitializer, "_install_framework_apps"),
        patch.object(BenchInitializer, "_configure_redis"),
        patch.object(BenchInitializer, "_ensure_admin_frontend") as admin_frontend,
        patch.object(PythonEnvManager, "install_node"),
        patch.object(PythonEnvManager, "install_node_dependencies"),
        patch.object(PythonEnvManager, "build_assets_for_app", side_effect=RuntimeError("build failed")),
        pytest.raises(RuntimeError, match="build failed"),
    ):
        BenchInitializer(bench).run(messages.append)

    assert any("Build app assets" in message for message in messages)
    assert any("Rolling back changes" in message for message in messages)
    assert (bench.apps_path / "local-work.txt").read_text() == "keep me"
    assert (bench.sites_path / "existing.txt").read_text() == "keep me too"
    assert not (bench.sites_path / "assets").exists()
    assert not bench.logs_path.exists()
    assert BenchConfig.toml_path(tmp_path).exists()
    admin_frontend.assert_not_called()


def test_init_rollback_removes_only_newly_cloned_apps(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config.apps.append(AppConfig(name="local", repo="https://example.com/local", branch="main"))
    bench.apps_path.mkdir()
    framework = bench.apps_path / "frappe"
    (framework / ".git").mkdir(parents=True)
    (framework / "local-work.txt").write_text("keep me")
    initializer = BenchInitializer(bench)

    def clone(app):
        app.path.mkdir()
        (app.path / "partial.txt").write_text("partial")
        raise RuntimeError("clone failed")

    with (
        patch("pilot.core.app.App.clone", autospec=True, side_effect=clone),
        patch("pilot.managers.environment.PythonEnvManager.install_app"),
        patch.object(BenchInitializer, "_ensure_admin_password"),
        patch.object(BenchInitializer, "_ensure_database_credentials"),
        patch.object(BenchInitializer, "_install_system_packages"),
        patch.object(BenchInitializer, "_create_virtualenv"),
        pytest.raises(RuntimeError, match="clone failed"),
    ):
        initializer.run(lambda message: None)

    assert (framework / "local-work.txt").read_text() == "keep me"
    assert not (bench.apps_path / "local").exists()
    assert bench.apps_path.exists()
