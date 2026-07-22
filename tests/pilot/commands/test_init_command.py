"""BenchInitializer._provision_or_verify: existing database handling."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pilot.core.bench.initializer import BenchInitializer
from pilot.exceptions import BenchError


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
