"""`pilot init`: existing database handling, the --no-dev opt-out, and --commit."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.commands.bench.initialize import InitCommand
from pilot.config import BenchConfig
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


def test_commit_pins_the_framework_app_on_its_branch(tmp_path: Path) -> None:
    from pilot.core.app import App

    bench = make_bench(tmp_path)
    commit = "4515014c87ece6cb20ee91d2cfb121c5cbd1ff4a"

    with (
        patch.object(App, "clone") as clone,
        patch.object(App, "checkout_commit") as checkout_commit,
        patch.object(Bench, "write_apps_txt"),
    ):
        BenchInitializer(bench, commit)._install_framework_apps(MagicMock(), lambda message: None)

    clone.assert_called_once_with()
    checkout_commit.assert_called_once_with(commit)
    assert bench.config.framework_app.branch == "version-16"


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


def test_a_failed_init_keeps_the_task_records(tmp_path: Path) -> None:
    """The wizard runs init as a task; deleting `tasks/` cut its event stream ("Lost connection")."""
    bench = make_bench(tmp_path)
    record = bench.path / "tasks" / "20261015-000000-abcdef" / "meta.json"
    record.parent.mkdir(parents=True)
    record.write_text("{}")
    initializer = BenchInitializer(bench)

    def create_structure_then_fail(on_progress) -> None:
        initializer._create_bench_structure()
        raise BenchError("clone failed")

    with patch.object(initializer, "_run_steps", create_structure_then_fail), pytest.raises(BenchError):
        initializer.run()

    assert record.exists()
    assert not bench.apps_path.exists()


def test_failed_init_preserves_existing_bench_data(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    records = [bench.sites_path / "test.local" / "site_config.json", bench.apps_path / "custom" / "data"]
    for record in records:
        record.parent.mkdir(parents=True)
        record.write_text("preserve me")
    initializer = BenchInitializer(bench)

    def create_structure_then_fail(on_progress) -> None:
        initializer._create_bench_structure()
        raise BenchError("install failed")

    with patch.object(initializer, "_run_steps", create_structure_then_fail), pytest.raises(BenchError):
        initializer.run()

    assert all(record.read_text() == "preserve me" for record in records)


def test_interrupted_init_does_not_publish_a_partial_environment(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.config.write(bench.path)
    initializer = BenchInitializer(bench)

    def create_environment_then_interrupt(on_progress) -> None:
        bench.python.parent.mkdir(parents=True)
        bench.python.touch()
        raise KeyboardInterrupt

    with (
        patch.object(initializer, "_run_steps", create_environment_then_interrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        initializer.run()

    assert bench.python.exists()
    assert not Bench(bench.path).is_initialized
    with patch.object(initializer, "_run_steps"):
        initializer.run()
    assert Bench(bench.path).is_initialized


def test_init_builds_the_framework_assets(tmp_path: Path) -> None:
    """A get-app before the first new-site links `sites/assets/frappe` without building it."""
    bench = make_bench(tmp_path)
    initializer = BenchInitializer(bench)
    steps = [
        "_ensure_admin_password",
        "_ensure_database_credentials",
        "_install_system_packages",
        "_create_bench_structure",
        "_create_virtualenv",
        "_install_framework_apps",
        "_configure_redis",
        "_ensure_admin_frontend",
        "_generate_process_config",
    ]
    with (
        patch.multiple(initializer, **{step: MagicMock() for step in steps}),
        patch.object(bench.config, "validate"),
        patch("pilot.managers.environment.PythonEnvManager") as python_env_manager,
    ):
        initializer._run_steps(lambda message: None)

    built_app = python_env_manager.return_value.build_assets_for_app.call_args.args[0]
    assert built_app.config.name == bench.config.framework_app.name
