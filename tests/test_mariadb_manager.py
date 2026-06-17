from __future__ import annotations

from unittest.mock import patch

from bench_cli.config.mariadb_config import MariaDBConfig
from bench_cli.managers.mariadb_manager import MariaDBManager


def _manager(password: str = "root") -> MariaDBManager:
    return MariaDBManager(MariaDBConfig(root_password=password))


# ── _sql_quote ────────────────────────────────────────────────────────────────


def test_sql_quote_plain() -> None:
    assert MariaDBManager._sql_quote("hunter2") == "'hunter2'"


def test_sql_quote_escapes_single_quote() -> None:
    assert MariaDBManager._sql_quote("a'b") == "'a\\'b'"


def test_sql_quote_escapes_backslash() -> None:
    assert MariaDBManager._sql_quote("a\\b") == "'a\\\\b'"


# ── secure_installation ───────────────────────────────────────────────────────


def test_secure_installation_noop_when_credentials_valid() -> None:
    manager = _manager()
    with patch.object(manager, "check_credentials", return_value=True), patch.object(
        manager, "_run_sql_as_superuser"
    ) as run_sql:
        manager.secure_installation()
    run_sql.assert_not_called()


def test_secure_installation_sets_password_and_hardens() -> None:
    manager = _manager("s3cret")
    with patch.object(manager, "check_credentials", return_value=False), patch.object(
        manager, "_run_sql_as_superuser"
    ) as run_sql:
        manager.secure_installation()
    run_sql.assert_called_once()
    sql = run_sql.call_args[0][0]
    assert "ALTER USER 'root'@'localhost' IDENTIFIED BY 's3cret';" in sql
    assert "DROP USER IF EXISTS ''@'localhost';" in sql
    assert "DROP DATABASE IF EXISTS test;" in sql
    assert "FLUSH PRIVILEGES;" in sql


# ── is_running ───────────────────────────────────────────────────────────────


def test_is_running_macos_true() -> None:
    brew_output = "Name      Status  User File\nmariadb@11.8  started root ~\n"
    with patch("bench_cli.managers.mariadb_manager.is_macos", return_value=True), patch(
        "subprocess.run", return_value=type("R", (), {"stdout": brew_output, "returncode": 0})()
    ):
        assert _manager().is_running() is True


def test_is_running_macos_false() -> None:
    brew_output = "Name      Status  User File\nmariadb@11.8  stopped root ~\n"
    with patch("bench_cli.managers.mariadb_manager.is_macos", return_value=True), patch(
        "subprocess.run", return_value=type("R", (), {"stdout": brew_output, "returncode": 0})()
    ):
        assert _manager().is_running() is False


def test_is_running_linux_true() -> None:
    with patch("bench_cli.managers.mariadb_manager.is_macos", return_value=False), patch(
        "subprocess.run", return_value=type("R", (), {"stdout": "active\n", "returncode": 0})()
    ):
        assert _manager().is_running() is True


def test_is_running_linux_false() -> None:
    with patch("bench_cli.managers.mariadb_manager.is_macos", return_value=False), patch(
        "subprocess.run", return_value=type("R", (), {"stdout": "inactive\n", "returncode": 3})()
    ):
        assert _manager().is_running() is False


def test_start_macos_skips_when_already_running() -> None:
    manager = _manager()
    with patch("bench_cli.managers.mariadb_manager.is_macos", return_value=True), patch.object(
        manager, "is_running", return_value=True
    ), patch("bench_cli.managers.mariadb_manager.run_command") as run_cmd:
        manager.start()
    run_cmd.assert_not_called()


def test_start_macos_starts_when_not_running() -> None:
    manager = _manager()
    with patch("bench_cli.managers.mariadb_manager.is_macos", return_value=True), patch.object(
        manager, "is_running", return_value=False
    ), patch.object(manager, "_brew_package", return_value="mariadb@11.8"), patch(
        "bench_cli.managers.mariadb_manager.run_command"
    ) as run_cmd:
        manager.start()
    run_cmd.assert_called_once_with(["brew", "services", "start", "mariadb@11.8"])


# ── check_credentials ─────────────────────────────────────────────────────────


def test_check_credentials_true_on_successful_connect() -> None:
    manager = _manager()
    with patch.object(manager, "_connect") as connect:
        assert manager.check_credentials("pw") is True
    connect.assert_called_once_with("pw")


def test_check_credentials_false_on_error() -> None:
    import pymysql

    manager = _manager()
    with patch.object(manager, "_connect", side_effect=pymysql.Error("denied")):
        assert manager.check_credentials("wrong") is False


# ── /api/setup/validate-mariadb endpoint ──────────────────────────────────────


def _client(tmp_path):
    from admin.backend.app import create_app

    app = create_app(tmp_path)
    app.config["TESTING"] = True
    return app.test_client()


def _post_validate(client, password: str):
    return client.post("/api/setup/validate-mariadb", json={"mariadb_password": password})


def test_validate_endpoint_will_install_when_not_installed(tmp_path) -> None:
    with patch("bench_cli.managers.mariadb_manager.MariaDBManager.is_installed", return_value=False):
        resp = _post_validate(_client(tmp_path), "anything")
    assert resp.get_json() == {"state": "will_install"}


def test_validate_endpoint_valid(tmp_path) -> None:
    with patch("bench_cli.managers.mariadb_manager.MariaDBManager.is_installed", return_value=True), patch(
        "bench_cli.managers.mariadb_manager.MariaDBManager.check_credentials", return_value=True
    ):
        resp = _post_validate(_client(tmp_path), "correct")
    assert resp.get_json() == {"state": "valid"}


def test_validate_endpoint_invalid(tmp_path) -> None:
    with patch("bench_cli.managers.mariadb_manager.MariaDBManager.is_installed", return_value=True), patch(
        "bench_cli.managers.mariadb_manager.MariaDBManager.check_credentials", return_value=False
    ):
        resp = _post_validate(_client(tmp_path), "wrong")
    assert resp.get_json() == {"state": "invalid"}
