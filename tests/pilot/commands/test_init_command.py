"""`pilot init`: existing database handling and the --no-dev opt-out."""

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
