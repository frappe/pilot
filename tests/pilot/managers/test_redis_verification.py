from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pilot.config import RedisConfig
from pilot.core.bench.initializer import BenchInitializer
from pilot.exceptions import BenchError
from pilot.managers.redis import RedisManager


def test_verify_installed_passes_when_redis_is_available() -> None:
    manager = RedisManager(RedisConfig(), MagicMock())

    with patch.object(manager, "is_installed", return_value=True):
        manager.verify_installed()


def test_verify_installed_raises_actionable_error_when_redis_is_missing() -> None:
    manager = RedisManager(RedisConfig(), MagicMock())

    with (
        patch.object(manager, "is_installed", return_value=False),
        pytest.raises(BenchError, match="Redis is not installed") as exc,
    ):
        manager.verify_installed()

    message = str(exc.value)
    assert "install.sh" in message
    assert "Redis/Valkey" in message


def test_verify_installed_never_attempts_package_installation() -> None:
    manager = RedisManager(RedisConfig(), MagicMock())

    with (
        patch.object(manager, "is_installed", return_value=False),
        patch("pilot.managers.redis.get_package_manager") as get_package_manager,
        pytest.raises(BenchError),
    ):
        manager.verify_installed()

    get_package_manager.assert_not_called()


def test_initializer_verifies_redis_without_installing_it() -> None:
    bench = MagicMock()
    bench.config.db_type = "sqlite"
    bench.config.redis = RedisConfig()
    initializer = BenchInitializer(bench)
    redis_manager = MagicMock()

    with (
        patch("pilot.managers.packages.get_package_manager", return_value=MagicMock()),
        patch("pilot.managers.redis.RedisManager", return_value=redis_manager),
        patch.object(initializer, "_install_build_headers"),
        patch("pilot.managers.environment.PythonEnvManager.ensure_python"),
    ):
        initializer._install_system_packages()

    redis_manager.verify_installed.assert_called_once_with()
    redis_manager.install.assert_not_called()
