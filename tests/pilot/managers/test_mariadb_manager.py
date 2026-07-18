from __future__ import annotations

import subprocess
from unittest.mock import patch, PropertyMock

import pytest

from pilot.config import MariaDBConfig
from pilot.exceptions import DatabaseError
from pilot.managers.database.mariadb import MariaDBManager

MODULE = "pilot.managers.database.mariadb"
BASE_MODULE = "pilot.managers.database.base"


def _manager(password: str = "root") -> MariaDBManager:
    return MariaDBManager(MariaDBConfig(root_password=password))


def test_socket_path_defaults_under_state_dir() -> None:
    assert _manager().socket_path.endswith("/.local/share/pilot/mariadb/mysqld.sock")


def test_socket_path_honors_explicit_value() -> None:
    assert MariaDBManager(MariaDBConfig(socket_path="/tmp/custom.sock")).socket_path == "/tmp/custom.sock"


def test_existing_defaults_to_false() -> None:
    assert MariaDBConfig().existing is False


def test_existing_is_not_inferred_from_host() -> None:
    assert MariaDBConfig(host="db.example.com").existing is False


def test_install_raises_when_missing_on_linux() -> None:
    m = _manager()
    with patch.object(m, "is_installed", return_value=False), \
         patch(f"{BASE_MODULE}.is_macos", return_value=False):
        with pytest.raises(DatabaseError, match="install.sh"):
            m.install()


def test_start_targets_systemctl_user_on_linux() -> None:
    m = _manager()
    with patch(f"{BASE_MODULE}.is_macos", return_value=False), \
         patch(f"{BASE_MODULE}.run_command") as rc:
        m.start()
    assert rc.call_args.args[0] == ["systemctl", "--user", "start", "pilot-mariadb.service"]


def test_provision_initialises_and_installs_unit_when_fresh(tmp_path) -> None:
    m = _manager()
    with patch(f"{MODULE}.is_macos", return_value=False), \
         patch.object(m, "install"), patch.object(type(m), "data_dir", new_callable=PropertyMock, return_value=tmp_path / "data"), \
         patch.object(m, "is_provisioned", return_value=False), \
         patch.object(m, "is_running", return_value=False), \
         patch.object(m, "_install_unit") as install_unit, \
         patch.object(m, "_wait_until_reachable"), patch.object(m, "secure_installation") as secure, \
         patch(f"{MODULE}.run_command") as rc:
        m.provision()
    install_unit.assert_called_once()
    secure.assert_called_once()
    argv_calls = [c.args[0] for c in rc.call_args_list]
    assert any("mariadb-install-db" in argv for argv in argv_calls)


def test_is_provisioned_on_macos_checks_live_server_not_a_marker_file() -> None:
    """macOS provisioning state comes from the live secured server."""
    m = _manager()
    with patch(f"{MODULE}.is_macos", return_value=True), patch.object(m, "is_running", return_value=False):
        assert m.is_provisioned() is False  # not running yet
    with patch(f"{MODULE}.is_macos", return_value=True), \
         patch.object(m, "is_running", return_value=True), \
         patch.object(m, "is_unsecured", return_value=True):
        assert m.is_provisioned() is False  # up but still passwordless
    with patch(f"{MODULE}.is_macos", return_value=True), \
         patch.object(m, "is_running", return_value=True), \
         patch.object(m, "is_unsecured", return_value=False):
        assert m.is_provisioned() is True  # up and already secured


def test_provision_reuses_already_provisioned_server() -> None:
    m = _manager()
    with patch(f"{MODULE}.is_macos", return_value=False), \
         patch.object(m, "install"), patch.object(m, "is_provisioned", return_value=True), \
         patch.object(m, "is_running", return_value=True), \
         patch.object(m, "_install_unit") as install_unit, \
         patch.object(m, "_wait_until_reachable"), patch.object(m, "secure_installation") as secure, \
         patch(f"{MODULE}.run_command") as rc:
        m.provision()
    install_unit.assert_not_called()
    rc.assert_not_called()
    secure.assert_called_once()


def test_sql_quote_plain() -> None:
    assert MariaDBManager._sql_quote("hunter2") == "'hunter2'"


def test_sql_quote_escapes_single_quote() -> None:
    assert MariaDBManager._sql_quote("a'b") == "'a\\'b'"


def test_sql_quote_escapes_backslash() -> None:
    assert MariaDBManager._sql_quote("a\\b") == "'a\\\\b'"


def test_secure_installation_noop_when_credentials_valid() -> None:
    manager = _manager()
    with patch.object(manager, "check_credentials", return_value=True), patch.object(
        manager, "_run_sql_as_superuser"
    ) as run_sql:
        manager.secure_installation()
    run_sql.assert_not_called()


def test_secure_installation_creates_and_grants() -> None:
    manager = _manager("s3cret")
    with patch.object(manager, "check_credentials", return_value=False), patch.object(
        manager, "_run_sql_as_superuser"
    ) as run_sql:
        manager.secure_installation()
    run_sql.assert_called_once()
    sql = run_sql.call_args[0][0]
    assert "CREATE USER IF NOT EXISTS 'root'@'localhost' IDENTIFIED BY 's3cret';" in sql
    assert "ALTER USER 'root'@'localhost' IDENTIFIED BY 's3cret';" in sql
    assert "GRANT ALL PRIVILEGES ON *.* TO 'root'@'localhost' WITH GRANT OPTION;" in sql
    assert "DROP USER IF EXISTS ''@'localhost';" in sql
    assert "DROP DATABASE IF EXISTS test;" in sql
    assert "FLUSH PRIVILEGES;" in sql


def test_secure_installation_escapes_malicious_admin_user() -> None:
    """admin_user is SQL-quoted before secure-installation statements."""
    config = MariaDBConfig(root_password="pw", admin_user="root'; DROP TABLE mysql.user; --")
    manager = MariaDBManager(config)
    with patch.object(manager, "check_credentials", return_value=False), patch.object(
        manager, "_run_sql_as_superuser"
    ) as run_sql:
        manager.secure_installation()
    sql = run_sql.call_args[0][0]
    # The attacker's quote must be escaped, not break out of the string literal.
    assert "root\\'; DROP TABLE mysql.user; --" in sql
    assert "CREATE USER IF NOT EXISTS 'root'" not in sql


def test_run_sql_as_superuser_no_sudo() -> None:
    m = _manager()
    with patch(f"{MODULE}.is_macos", return_value=False), patch(f"{MODULE}.subprocess.run") as run:
        m._run_sql_as_superuser("SELECT 1;")
    cmd = run.call_args[0][0]
    assert "sudo" not in cmd
    assert cmd[0] == "mariadb"


def test_is_reachable_on_macos_ignores_local_socket_path() -> None:
    """socket_path() (our own _STATE_DIR) is never created on macOS — only
    is_running() is a meaningful signal there."""
    m = _manager()
    with patch.object(m, "is_running", return_value=True), patch(f"{MODULE}.is_macos", return_value=True):
        assert m.is_reachable() is True


def test_run_sql_as_superuser_omits_local_socket_on_macos() -> None:
    """Homebrew's mariadb client owns socket resolution on macOS —
    socket_path() (our own _STATE_DIR) is never created there."""
    m = _manager()
    with patch(f"{MODULE}.is_macos", return_value=True), patch(f"{MODULE}.subprocess.run") as run:
        m._run_sql_as_superuser("SELECT 1;")
    cmd = run.call_args[0][0]
    assert cmd == ["mariadb"]


def test_check_credentials_true_on_successful_connect() -> None:
    manager = _manager()
    with patch(f"{MODULE}.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess([], 0)
        assert manager.check_credentials("pw") is True
    # Password is passed via MYSQL_PWD env, never argv.
    args, kwargs = run.call_args
    assert "pw" not in args[0]
    assert kwargs["env"]["MYSQL_PWD"] == "pw"


def test_check_credentials_false_on_error() -> None:
    manager = _manager()
    with patch(f"{MODULE}.subprocess.run") as run:
        run.return_value = subprocess.CompletedProcess([], 1)
        assert manager.check_credentials("wrong") is False


def test_check_credentials_times_out() -> None:
    manager = _manager()
    with patch(
        f"{MODULE}.subprocess.run",
        side_effect=subprocess.TimeoutExpired("mariadb", 5),
    ):
        assert manager.check_credentials("wrong") is False


def _client(tmp_path):
    from admin.backend.app import create_app

    app = create_app(tmp_path)
    app.config["TESTING"] = True
    return app.test_client()


def _post_validate(client, password: str):
    return client.post(
        "/api/v1/setup/database-validations",
        json={"engine": "mariadb", "password": password},
    )


def test_validate_endpoint_will_install_when_not_installed(tmp_path) -> None:
    with patch(f"{MODULE}.MariaDBManager.is_installed", return_value=False):
        resp = _post_validate(_client(tmp_path), "anything")
    assert resp.get_json() == {"engine": "mariadb", "state": "will_install"}


def test_validate_endpoint_will_install_when_not_provisioned(tmp_path) -> None:
    with patch(f"{MODULE}.MariaDBManager.is_installed", return_value=True), \
         patch(f"{MODULE}.MariaDBManager.is_provisioned", return_value=False):
        resp = _post_validate(_client(tmp_path), "anything")
    assert resp.get_json() == {"engine": "mariadb", "state": "will_install"}


def test_validate_endpoint_valid(tmp_path) -> None:
    with patch(f"{MODULE}.MariaDBManager.is_installed", return_value=True), \
         patch(f"{MODULE}.MariaDBManager.is_provisioned", return_value=True), \
         patch(f"{MODULE}.MariaDBManager.check_credentials", return_value=True):
        resp = _post_validate(_client(tmp_path), "correct")
    assert resp.get_json() == {"engine": "mariadb", "state": "valid"}


def test_validate_endpoint_invalid(tmp_path) -> None:
    with patch(f"{MODULE}.MariaDBManager.is_installed", return_value=True), \
         patch(f"{MODULE}.MariaDBManager.is_provisioned", return_value=True), \
         patch(f"{MODULE}.MariaDBManager.check_credentials", return_value=False):
        resp = _post_validate(_client(tmp_path), "wrong")
    assert resp.get_json() == {"engine": "mariadb", "state": "invalid"}


def test_validate_endpoint_on_macos_checks_password_for_already_secured_server(tmp_path) -> None:
    """macOS validation checks credentials on an already-secured server."""
    with patch(f"{MODULE}.is_macos", return_value=True), \
         patch(f"{MODULE}.MariaDBManager.is_installed", return_value=True), \
         patch(f"{MODULE}.MariaDBManager.is_running", return_value=True), \
         patch(f"{MODULE}.MariaDBManager.is_unsecured", return_value=False), \
         patch(f"{MODULE}.MariaDBManager.check_credentials", return_value=False):
        resp = _post_validate(_client(tmp_path), "wrong")
    assert resp.get_json() == {"engine": "mariadb", "state": "invalid"}
