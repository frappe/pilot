"""Tests for MariaDBManager.is_running() and the guarded start()."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from bench_cli.config.mariadb_config import MariaDBConfig
from bench_cli.managers.mariadb_manager import MariaDBManager


def make_manager(host: str = "localhost", port: int = 3306) -> MariaDBManager:
    return MariaDBManager(MariaDBConfig(host=host, port=port))


# ── is_running() ──────────────────────────────────────────────────────────────


def test_is_running_returns_true_when_port_is_open() -> None:
    manager = make_manager()
    mock_conn = MagicMock()
    with patch("socket.create_connection", return_value=mock_conn) as mock_create:
        assert manager.is_running() is True
        mock_create.assert_called_once_with(("localhost", 3306), timeout=1)


def test_is_running_returns_false_when_connection_refused() -> None:
    manager = make_manager()
    with patch("socket.create_connection", side_effect=OSError("Connection refused")):
        assert manager.is_running() is False


def test_is_running_returns_false_on_timeout() -> None:
    manager = make_manager()
    with patch("socket.create_connection", side_effect=OSError("timed out")):
        assert manager.is_running() is False


def test_is_running_uses_configured_host_and_port() -> None:
    manager = make_manager(host="127.0.0.1", port=3307)
    mock_conn = MagicMock()
    with patch("socket.create_connection", return_value=mock_conn) as mock_create:
        manager.is_running()
        mock_create.assert_called_once_with(("127.0.0.1", 3307), timeout=1)


# ── start() ───────────────────────────────────────────────────────────────────


def test_start_skips_service_when_already_running() -> None:
    manager = make_manager()
    with patch.object(manager, "is_running", return_value=True):
        with patch("bench_cli.managers.mariadb_manager.run_command") as mock_run:
            manager.start()
            mock_run.assert_not_called()


def test_start_calls_brew_on_macos_when_not_running() -> None:
    manager = make_manager()
    with patch.object(manager, "is_running", return_value=False):
        with patch("bench_cli.managers.mariadb_manager.is_macos", return_value=True):
            with patch("bench_cli.managers.mariadb_manager.run_command") as mock_run:
                manager.start()
                mock_run.assert_called_once_with(["brew", "services", "start", "mariadb"])


def test_start_calls_brew_with_versioned_package() -> None:
    manager = MariaDBManager(MariaDBConfig(host="localhost", port=3306, version="10.6"))
    with patch.object(manager, "is_running", return_value=False):
        with patch("bench_cli.managers.mariadb_manager.is_macos", return_value=True):
            with patch("bench_cli.managers.mariadb_manager.run_command") as mock_run:
                manager.start()
                mock_run.assert_called_once_with(["brew", "services", "start", "mariadb@10.6"])


def test_start_calls_systemctl_on_linux_when_not_running() -> None:
    manager = make_manager()
    with patch.object(manager, "is_running", return_value=False):
        with patch("bench_cli.managers.mariadb_manager.is_macos", return_value=False):
            with patch("bench_cli.managers.mariadb_manager.run_command") as mock_run:
                manager.start()
                mock_run.assert_called_once_with(["sudo", "systemctl", "start", "mariadb"])
